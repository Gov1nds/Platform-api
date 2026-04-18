"""
Guest intelligence report endpoint — the free-tier lead-gen funnel.

POST /guest/intelligence-report
  - No authentication required
  - Rate limited: 5 requests/IP/hour via Redis
  - Returns enriched components, redacted vendor shortlist, strategy summary
  - Writes to GuestSearchLog

References: Blueprint Section 2.3
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.guest import (
    EnrichedComponent,
    FreshnessAnnotation,
    GuestIntelligenceRequest,
    GuestIntelligenceResponse,
    LockedFeatureTeaser,
    PriceEstimate,
    RedactedVendor,
    RiskFlag,
    StrategyOption,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guest", tags=["Guest"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


async def _check_rate_limit(request: Request) -> None:
    """Enforce 5 requests/IP/hour using Redis."""
    try:
        from app.main import _redis_client
        if _redis_client is None:
            return  # Redis unavailable → degrade gracefully
        ip = _get_client_ip(request)
        key = f"guest_rate:{ip}"
        count = await _redis_client.incr(key)
        if count == 1:
            await _redis_client.expire(key, 3600)
        if count > 5:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 5 intelligence reports per hour.")
    except HTTPException:
        raise
    except Exception:
        logger.debug("Rate limit check failed, allowing request")


@router.post("/intelligence-report", response_model=GuestIntelligenceResponse)
async def guest_intelligence_report(
    body: GuestIntelligenceRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Free-tier intelligence report. Returns enriched components, redacted
    vendor shortlist, and sourcing strategy summary.

    Rate limited to 5 requests/IP/hour. No authentication required.
    """
    await _check_rate_limit(request)

    # 1. Create/update guest session
    from app.services.guest_service import get_or_create_guest_session, increment_search_count
    session_token = body.session_token or request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    guest_session = get_or_create_guest_session(db, session_token)
    new_token = str(guest_session.id)

    # 2. Detect location if not provided
    ip = _get_client_ip(request)
    delivery_location = body.delivery_location

    # 3. Build enriched components (call Repo B if available, else best-effort)
    enriched_components: list[EnrichedComponent] = []
    freshness_annotations: list[FreshnessAnnotation] = []

    for raw_text in body.components:
        component = await _enrich_component(db, raw_text, delivery_location, body.currency)
        enriched_components.append(component)

    # 4. Get vendor shortlist (redacted)
    vendor_shortlist = _get_redacted_vendors(db, enriched_components, delivery_location)

    # 5. Build strategy summary
    strategy_summary = _build_strategy(enriched_components, delivery_location, body.currency)

    # 6. Build freshness report
    freshness_report = _build_freshness_report(db)

    # 7. Locked features teaser
    locked_features = [
        LockedFeatureTeaser(feature="vendor_contact_info", description="Sign in to see vendor contact details and request quotes"),
        LockedFeatureTeaser(feature="detailed_scoring", description="Sign in for full vendor scoring breakdown and comparison matrix"),
        LockedFeatureTeaser(feature="rfq_creation", description="Sign in to create RFQs and receive competitive quotes"),
        LockedFeatureTeaser(feature="order_management", description="Sign in to manage purchase orders and track shipments"),
        LockedFeatureTeaser(feature="saved_projects", description="Sign in to save your research as a project"),
    ]

    # 8. Log search
    try:
        from app.services.guest_service import increment_search_count, increment_component_count
        increment_search_count(db, guest_session)
        increment_component_count(db, guest_session, len(body.components))
    except Exception:
        logger.debug("Failed to log guest search", exc_info=True)

    # Set session cookie
    response.set_cookie(
        key=settings.GUEST_SESSION_COOKIE_NAME,
        value=new_token,
        max_age=settings.GUEST_SESSION_MAX_AGE_DAYS * 86400,
        httponly=True,
        samesite="lax",
    )

    return GuestIntelligenceResponse(
        components=enriched_components,
        vendor_shortlist=vendor_shortlist,
        strategy_summary=strategy_summary,
        freshness_report=freshness_report,
        locked_features=locked_features,
        session_token=new_token,
    )


# ── Helper functions ─────────────────────────────────────────────────────────

async def _enrich_component(
    db: Session, raw_text: str, delivery_location: str, currency: str,
) -> EnrichedComponent:
    """Enrich a single component — calls Repo B if available, else DB lookup."""
    try:
        from app.services.analyzer_service import call_normalize
        result = await _try_normalize(raw_text)
        if result:
            return EnrichedComponent(
                raw_text=raw_text,
                canonical_name=result.get("canonical_name"),
                category=result.get("category"),
                commodity_group=result.get("commodity_group"),
                confidence=result.get("confidence", 0.0),
                price_estimate=PriceEstimate(
                    unit_price=result.get("estimated_price", 0.0),
                    currency=currency,
                    confidence=0.5,
                    source="benchmark",
                    data_quality_label="ESTIMATED",
                ),
            )
    except Exception:
        logger.debug("Repo B normalize failed for '%s'", raw_text)

    # Fallback: return raw with DEGRADED label
    return EnrichedComponent(
        raw_text=raw_text,
        canonical_name=raw_text,
        category="unknown",
        commodity_group="general",
        confidence=0.0,
        price_estimate=PriceEstimate(
            unit_price=0.0,
            currency=currency,
            confidence=0.0,
            source="benchmark",
            data_quality_label="DEGRADED_ESTIMATE",
        ),
        risk_flags=[RiskFlag(flag_type="LOW_CONFIDENCE", severity="low", description="Component could not be normalized")],
    )


async def _try_normalize(raw_text: str) -> dict | None:
    """Attempt to call Repo B normalize endpoint."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.BOM_ANALYZER_URL}/api/v1/normalize",
                json={"rows": [{"raw_text": raw_text}]},
                headers={"X-Service-Token": settings.INTERNAL_API_KEY},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                return results[0] if results else None
    except Exception:
        return None


def _get_redacted_vendors(
    db: Session, components: list[EnrichedComponent], delivery_location: str,
) -> list[RedactedVendor]:
    """Fetch top vendors and redact contact info."""
    try:
        from app.models.vendor import Vendor
        vendors = db.query(Vendor).filter(
            Vendor.is_active == True,
        ).order_by(Vendor.reliability_score.desc()).limit(3).all()

        result = []
        for i, v in enumerate(vendors, 1):
            name = getattr(v, "name", "Vendor")
            # Partially mask name for guest view
            display = name[:3] + "***" if len(name) > 3 else name
            result.append(RedactedVendor(
                vendor_id=str(v.id),
                display_name=display,
                country=getattr(v, "country", None),
                reliability_score=float(getattr(v, "reliability_score", 0) or 0),
                total_score=float(getattr(v, "reliability_score", 0) or 0),
                rank=i,
                score_breakdown={"reliability": float(getattr(v, "reliability_score", 0) or 0)},
                data_quality_label="DEGRADED_ESTIMATE",
            ))
        return result
    except Exception:
        logger.debug("Vendor lookup failed", exc_info=True)
        return []


def _build_strategy(
    components: list[EnrichedComponent], delivery_location: str, currency: str,
) -> list[StrategyOption]:
    """Build local vs international strategy comparison."""
    return [
        StrategyOption(mode="local", estimated_tlc=0.0, lead_time_days=7, currency=currency),
        StrategyOption(mode="international", estimated_tlc=0.0, lead_time_days=30, currency=currency),
    ]


def _build_freshness_report(db: Session) -> list[FreshnessAnnotation]:
    """Build freshness annotations for guest report."""
    try:
        from app.services.freshness_service import freshness_service
        report = freshness_service.build_freshness_report(db)
        return [
            FreshnessAnnotation(
                table=tbl,
                status=info.get("status", "UNKNOWN"),
                ttl_minutes=info.get("ttl_minutes", 0),
            )
            for tbl, info in report.items()
        ]
    except Exception:
        return []
