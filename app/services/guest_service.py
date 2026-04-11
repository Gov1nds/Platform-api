"""
Centralized guest session management.

Handles HttpOnly cookie issuance/lookup, rate limiting,
geolocation detection, and guest-to-authenticated-user merge.

References: GAP-001 (HttpOnly cookie), GAP-014 (rate limiting),
            GAP-030 (cleanup lifecycle), architecture.md Domain 5
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.enums import GuestSessionStatus
from app.models.user import GuestSession

logger = logging.getLogger(__name__)

_UTC = timezone.utc


# ── Location / currency detection ────────────────────────────────────────────

def detect_location(ip_address: str) -> tuple[str | None, str | None]:
    """
    Attempt IP geolocation via MaxMind GeoIP2.

    Returns (location_string, currency_code) or (None, None) on failure.
    This is a best-effort hook; failures are non-fatal.
    """
    if not settings.MAXMIND_LICENSE_KEY:
        return None, None
    try:
        import geoip2.database  # type: ignore[import-untyped]

        # In production the DB file is downloaded and refreshed periodically.
        # For dev/test we return None gracefully.
        reader = geoip2.database.Reader("/var/lib/GeoIP/GeoLite2-City.mmdb")
        resp = reader.city(ip_address)
        country = resp.country.iso_code or ""
        city = resp.city.name or ""
        location = f"{city}, {country}".strip(", ")

        # Rudimentary country → currency mapping (extend as needed)
        currency_map = {
            "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "IN": "INR",
            "JP": "JPY", "CN": "CNY", "CA": "CAD", "AU": "AUD", "SG": "SGD",
        }
        currency = currency_map.get(country, "USD")
        reader.close()
        return location or None, currency
    except Exception:
        logger.debug("Geolocation lookup failed for %s", ip_address, exc_info=True)
        return None, None


# ── Cookie helpers ───────────────────────────────────────────────────────────

def _set_guest_cookie(response: Response, token: str) -> None:
    """Set the HttpOnly guest session cookie on the response."""
    response.set_cookie(
        key=settings.GUEST_SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.GUEST_SESSION_MAX_AGE_DAYS * 86400,
        path="/",
    )


def _clear_guest_cookie(response: Response) -> None:
    """Delete the guest session cookie."""
    response.delete_cookie(
        key=settings.GUEST_SESSION_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


# ── Core session operations ──────────────────────────────────────────────────

def create_guest_session(
    request: Request,
    response: Response,
    db: Session,
) -> GuestSession:
    """
    Create a new server-managed guest session and set the HttpOnly cookie.

    The session token is a server-generated UUID — never client-supplied.
    """
    token = str(uuid.uuid4())
    ip = request.client.host if request.client else "0.0.0.0"
    location, currency = detect_location(ip)
    now = datetime.now(_UTC)

    gs = GuestSession(
        session_token=token,
        status=GuestSessionStatus.ACTIVE,
        last_active_at=now,
        expires_at=now + timedelta(days=settings.GUEST_SESSION_MAX_AGE_DAYS),
        detected_location=location,
        detected_currency=currency or "USD",
        ip_address=ip,
        component_count=0,
        search_count=0,
    )
    db.add(gs)
    db.flush()

    _set_guest_cookie(response, token)
    logger.info("Created guest session %s from %s", gs.id, ip)
    return gs


def get_guest_session(
    request: Request,
    db: Session,
) -> GuestSession | None:
    """
    Look up an existing guest session from the HttpOnly cookie.

    Returns None if:
    - No cookie present
    - Token not found in DB
    - Session expired or not ACTIVE
    """
    token = request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    if not token:
        return None

    gs = (
        db.query(GuestSession)
        .filter(
            GuestSession.session_token == token,
            GuestSession.status == GuestSessionStatus.ACTIVE,
        )
        .first()
    )
    if not gs:
        return None

    # Expiry check
    if gs.expires_at and gs.expires_at < datetime.now(_UTC):
        gs.status = GuestSessionStatus.EXPIRED
        db.flush()
        return None

    # Sliding window: refresh last_active_at
    gs.last_active_at = datetime.now(_UTC)
    db.flush()
    return gs


def get_or_create_guest_session(
    request: Request,
    response: Response,
    db: Session,
) -> GuestSession:
    """Return existing guest session from cookie, or create a new one."""
    gs = get_guest_session(request, db)
    if gs:
        return gs
    return create_guest_session(request, response, db)


# ── Rate limiting ────────────────────────────────────────────────────────────

def check_search_rate_limit(guest: GuestSession) -> bool:
    """
    Enforce max 10 searches per guest session per day.

    Returns True if allowed, False if over limit.
    Caller should raise 429 when False.
    """
    return guest.search_count < 10


def increment_search_count(db: Session, guest: GuestSession) -> None:
    """Increment the daily search counter on the guest session."""
    guest.search_count = (guest.search_count or 0) + 1
    guest.last_active_at = datetime.now(_UTC)
    db.flush()


def increment_component_count(db: Session, guest: GuestSession, count: int) -> None:
    """Track cumulative components analysed in this guest session."""
    guest.component_count = (guest.component_count or 0) + count
    db.flush()


# ── Guest → Authenticated merge ─────────────────────────────────────────────

def merge_guest_to_user(
    db: Session,
    guest_session_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> dict:
    """
    Transfer all guest-owned resources to the authenticated user.

    Updates:
    - boms.uploaded_by_user_id
    - projects.user_id + organization_id
    - analysis_results.user_id
    - rfq_batches.requested_by_user_id
    - search_sessions.user_id
    - sourcing_cases.user_id

    Also:
    - Sets guest_session.status = CONVERTED
    - Sets guest_session.merged_user_id / merged_at
    - Upgrades project visibility from guest_preview → owner_only
    - Grants owner ACL on merged projects
    """
    gs = db.query(GuestSession).filter(GuestSession.id == guest_session_id).first()
    if not gs:
        return {"merged": False, "reason": "guest_session_not_found"}

    gid = gs.id
    tables = [
        ("bom.boms", "uploaded_by_user_id", "guest_session_id"),
        ("projects.projects", "user_id", "guest_session_id"),
        ("bom.analysis_results", "user_id", "guest_session_id"),
        ("sourcing.rfq_batches", "requested_by_user_id", "guest_session_id"),
        ("projects.search_sessions", "user_id", "guest_session_id"),
        ("projects.sourcing_cases", "user_id", "guest_session_id"),
    ]

    counts: dict[str, int] = {}
    for tbl, uid_col, gsid_col in tables:
        try:
            params: dict = {"uid": user_id, "gid": gid}
            sql = (
                f"UPDATE {tbl} SET {uid_col}=:uid, updated_at=now() "
                f"WHERE {gsid_col}=:gid AND ({uid_col} IS NULL OR {uid_col}!=:uid)"
            )
            # Also stamp organization_id if the table has it
            if organization_id:
                sql = (
                    f"UPDATE {tbl} SET {uid_col}=:uid, organization_id=:org_id, updated_at=now() "
                    f"WHERE {gsid_col}=:gid AND ({uid_col} IS NULL OR {uid_col}!=:uid)"
                )
                params["org_id"] = organization_id

            r = db.execute(text(sql), params)
            counts[tbl.split(".")[-1]] = r.rowcount  # type: ignore[union-attr]
        except Exception:
            logger.warning("Merge update failed for %s", tbl, exc_info=True)
            counts[tbl.split(".")[-1]] = 0

    # Upgrade visibility on merged projects
    try:
        db.execute(
            text(
                "UPDATE projects.projects SET visibility='owner_only' "
                "WHERE user_id=:uid AND visibility='guest_preview'"
            ),
            {"uid": user_id},
        )
    except Exception:
        pass

    # Grant owner ACL on merged projects
    try:
        db.execute(
            text(
                "INSERT INTO projects.project_acl (id, project_id, principal_type, principal_id, role) "
                "SELECT gen_random_uuid()::text, id, 'user', :uid, 'owner' "
                "FROM projects.projects WHERE user_id=:uid "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id},
        )
    except Exception:
        pass

    # Mark guest session as converted
    gs.status = GuestSessionStatus.CONVERTED
    gs.merged_user_id = user_id
    gs.merged_at = datetime.now(_UTC)
    db.flush()

    logger.info("Merged guest %s → user %s (counts=%s)", gid, user_id, counts)
    return {"merged": True, "counts": counts}