"""
Authentication and session management routes.

Endpoints:
  POST /auth/token          — OAuth code exchange or password login
  POST /auth/refresh        — Refresh access token via HttpOnly cookie
  POST /auth/logout         — Clear refresh cookie
  GET  /auth/dashboard      — Dashboard hydration (user + org + KPIs)
  POST /auth/convert-guest  — Guest-to-authenticated conversion
  POST /auth/register       — Email/password registration (dev/transition)
  POST /auth/login          — Email/password login (dev/transition)
  GET  /auth/me             — Current user profile
  POST /auth/vendor/login   — Vendor password login
  POST /auth/vendor/register — Vendor self-registration

References: GAP-008 (OAuth/OIDC), GAP-001 (guest merge),
            api-contract-review.md Section 5.3
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.enums import GuestSessionStatus, ProjectStatus
from app.models.bom import BOM
from app.models.project import Project, SearchSession
from app.models.user import (
    GuestSession,
    Organization,
    OrganizationMembership,
    User,
    VendorUser,
)
from app.schemas import (
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    VendorTokenResponse,
    VendorUserLogin,
    VendorUserResponse,
)
from app.schemas.auth import (
    AuthUserResponse,
    DashboardKPIs,
    DashboardResponse,
    GuestConvertRequest,
    GuestConvertResponse,
    RefreshResponse,
    TokenExchangeRequest,
    TokenExchangeResponse,
    VendorRegisterRequest,
    VendorRegisterResponse,
)
from app.services import guest_service
from app.services.event_service import track
from app.utils.dependencies import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

_REFRESH_COOKIE = "pgi_refresh"
_REFRESH_MAX_AGE = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_token_claims(user: User) -> dict:
    return {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "type": "buyer",
        "organization_id": user.organization_id,
    }


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_REFRESH_MAX_AGE,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _get_or_create_org_for_user(db: Session, user: User) -> Organization | None:
    """Ensure user has an organization. Create a personal one if missing."""
    if user.organization_id:
        return db.query(Organization).filter(Organization.id == user.organization_id).first()

    org = Organization(
        name=f"{user.full_name or user.email}'s Organization",
        slug=f"org-{str(uuid.uuid4())[:8]}",
        type="buyer",
    )
    db.add(org)
    db.flush()

    user.organization_id = org.id
    db.add(OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
        role="ORGANIZATION_OWNER",
        accepted_at=datetime.now(timezone.utc),
    ))
    db.flush()
    return org


# ── POST /auth/token — OAuth code exchange or password login ────────────────

@router.post("/token", response_model=TokenExchangeResponse)
async def token_exchange(
    body: TokenExchangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Exchange credentials for access + refresh tokens.

    Supports:
    - grant_type=authorization_code → OAuth code exchange
    - grant_type=password → email/password (dev/transition)

    Access token returned in JSON body.
    Refresh token set as HttpOnly secure cookie.
    """
    user: User | None = None

    if body.grant_type == "password":
        if not body.email or not body.password:
            raise HTTPException(400, "email and password required for password grant")
        user = db.query(User).filter(User.email == body.email).first()
        if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")

    elif body.grant_type == "authorization_code":
        if not body.provider or not body.code:
            raise HTTPException(400, "provider and code required for authorization_code grant")
        # OAuth validation hook — full implementation deferred to INT-008 batch.
        # For now, raise 501 to signal the feature boundary.
        raise HTTPException(501, f"OAuth provider '{body.provider}' not yet configured")

    else:
        raise HTTPException(400, f"Unsupported grant_type: {body.grant_type}")

    if not user:
        raise HTTPException(401, "Authentication failed")

    # Ensure organization exists
    _get_or_create_org_for_user(db, user)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)

    # Guest merge if guest_session_id provided
    merge_result: dict = {}
    if body.guest_session_id:
        merge_result = guest_service.merge_guest_to_user(
            db, body.guest_session_id, user.id, user.organization_id,
        )

    # Also try cookie-based guest merge
    gs = guest_service.get_guest_session(request, db)
    if gs and gs.status == GuestSessionStatus.ACTIVE and not merge_result.get("merged"):
        merge_result = guest_service.merge_guest_to_user(
            db, gs.id, user.id, user.organization_id,
        )

    claims = _build_token_claims(user)
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)

    _set_refresh_cookie(response, refresh_token)
    guest_service._clear_guest_cookie(response)

    track(db, "login", actor_id=user.id, resource_type="user", resource_id=user.id)
    db.commit()

    return TokenExchangeResponse(
        access_token=access_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=AuthUserResponse.model_validate(user),
    )


# ── POST /auth/refresh ──────────────────────────────────────────────────────

@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchange refresh token cookie for a new access token. Rotates refresh token."""
    rt = request.cookies.get(_REFRESH_COOKIE)
    if not rt:
        raise HTTPException(401, "No refresh token")

    payload = decode_refresh_token(rt)
    if not payload:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = db.query(User).filter(User.id == payload.sub).first()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")

    claims = _build_token_claims(user)
    new_access = create_access_token(claims)
    new_refresh = create_refresh_token(claims)

    _set_refresh_cookie(response, new_refresh)

    return RefreshResponse(
        access_token=new_access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ── POST /auth/logout ───────────────────────────────────────────────────────

@router.post("/logout")
async def logout(response: Response):
    """Clear refresh token and guest session cookies."""
    _clear_refresh_cookie(response)
    guest_service._clear_guest_cookie(response)
    return {"status": "logged_out"}


# ── GET /auth/dashboard ─────────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardResponse)
def dashboard_hydration(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Single-call dashboard hydration.

    Returns user profile, organization, KPIs, action queue,
    and unread notification count.
    """
    org = None
    if user.organization_id:
        org_obj = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if org_obj:
            org = {
                "id": org_obj.id,
                "name": org_obj.name,
                "slug": org_obj.slug,
                "type": org_obj.type,
            }

    # KPIs scoped to organization
    org_id = user.organization_id
    active_projects = 0
    pending_rfqs = 0
    open_pos = 0
    pending_approvals = 0

    if org_id:
        from app.models.rfq import RFQBatch, PurchaseOrder, ApprovalRequest

        active_projects = (
            db.query(Project)
            .filter(
                Project.organization_id == org_id,
                Project.deleted_at.is_(None),
                Project.status.notin_(["CLOSED", "CANCELLED", "ARCHIVED"]),
            )
            .count()
        )
        try:
            pending_rfqs = (
                db.query(RFQBatch)
                .filter(RFQBatch.organization_id == org_id, RFQBatch.status == "DRAFT")
                .count()
            )
        except Exception:
            pending_rfqs = 0

        try:
            open_pos = (
                db.query(PurchaseOrder)
                .filter(
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrder.status.notin_(["CLOSED", "CANCELLED"]),
                )
                .count()
            )
        except Exception:
            open_pos = 0

        try:
            pending_approvals = (
                db.query(ApprovalRequest)
                .filter(
                    ApprovalRequest.organization_id == org_id,
                    ApprovalRequest.status == "PENDING",
                    ApprovalRequest.assigned_to_user_id == user.id,
                )
                .count()
            )
        except Exception:
            pending_approvals = 0

    # Unread notifications
    unread = 0
    try:
        from app.models.notification import Notification
        unread = (
            db.query(Notification)
            .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
            .count()
        )
    except Exception:
        pass

    return DashboardResponse(
        user=AuthUserResponse.model_validate(user),
        organization=org,
        kpis=DashboardKPIs(
            active_projects=active_projects,
            pending_rfqs=pending_rfqs,
            open_pos=open_pos,
            pending_approvals=pending_approvals,
        ),
        action_queue=[],
        unread_notifications=unread,
    )


# ── POST /auth/convert-guest ────────────────────────────────────────────────

@router.post("/convert-guest", response_model=GuestConvertResponse)
async def convert_guest(
    body: GuestConvertRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Same-page guest → authenticated conversion (GA-003).

    Creates a new user account, merges guest data, returns tokens.
    """
    # Check for existing email
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already registered")

    # Must have active guest session
    gs = guest_service.get_guest_session(request, db)
    if not gs:
        raise HTTPException(400, "No active guest session to convert")

    # Create user
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="BUYER_EDITOR",
    )
    db.add(user)
    db.flush()

    # Create org
    _get_or_create_org_for_user(db, user)

    # Merge guest → user
    merge_result = guest_service.merge_guest_to_user(
        db, gs.id, user.id, user.organization_id,
    )

    claims = _build_token_claims(user)
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)

    _set_refresh_cookie(response, refresh_token)
    guest_service._clear_guest_cookie(response)

    track(db, "signup", actor_id=user.id, resource_type="user", resource_id=user.id)
    db.commit()

    return GuestConvertResponse(
        access_token=access_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=AuthUserResponse.model_validate(user),
        merge_result=merge_result,
    )


# ── POST /auth/register (dev/transition — deprecated) ───────────────────────

@router.post("/register", response_model=TokenResponse, status_code=201,
             deprecated=True)
def register(
    body: UserRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already registered")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    db.flush()

    _get_or_create_org_for_user(db, user)

    # Merge guest if session_token provided (legacy path)
    merge: dict = {}
    if body.session_token:
        gs = db.query(GuestSession).filter(
            GuestSession.session_token == body.session_token
        ).first()
        if gs:
            merge = guest_service.merge_guest_to_user(
                db, gs.id, user.id, user.organization_id,
            )

    # Also try cookie-based guest merge
    gs_cookie = guest_service.get_guest_session(request, db)
    if gs_cookie and not merge.get("merged"):
        merge = guest_service.merge_guest_to_user(
            db, gs_cookie.id, user.id, user.organization_id,
        )

    track(db, "signup", actor_id=user.id, resource_type="user", resource_id=user.id)
    db.commit()

    claims = _build_token_claims(user)
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)
    _set_refresh_cookie(response, refresh_token)
    guest_service._clear_guest_cookie(response)

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user),
        merge_result=merge,
    )


# ── POST /auth/login (dev/transition — deprecated) ──────────────────────────

@router.post("/login", response_model=TokenResponse, deprecated=True)
def login(
    body: UserLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(401, "Invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)

    merge: dict = {}
    if body.session_token:
        gs = db.query(GuestSession).filter(
            GuestSession.session_token == body.session_token
        ).first()
        if gs:
            merge = guest_service.merge_guest_to_user(
                db, gs.id, user.id, user.organization_id,
            )

    gs_cookie = guest_service.get_guest_session(request, db)
    if gs_cookie and not merge.get("merged"):
        merge = guest_service.merge_guest_to_user(
            db, gs_cookie.id, user.id, user.organization_id,
        )

    db.commit()

    claims = _build_token_claims(user)
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)
    _set_refresh_cookie(response, refresh_token)
    guest_service._clear_guest_cookie(response)

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user),
        merge_result=merge,
    )


# ── GET /auth/me ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(require_user)):
    return UserResponse.model_validate(user)


# ── POST /auth/vendor/login ─────────────────────────────────────────────────

@router.post("/vendor/login", response_model=VendorTokenResponse)
def vendor_login(body: VendorUserLogin, db: Session = Depends(get_db)):
    vu = db.query(VendorUser).filter(VendorUser.email == body.email).first()
    if not vu or not verify_password(body.password, vu.password_hash):
        raise HTTPException(401, "Invalid vendor credentials")

    vu.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token({
        "sub": vu.id,
        "email": vu.email,
        "type": "vendor",
        "vendor_id": vu.vendor_id,
        "role": vu.role,
    })
    return VendorTokenResponse(
        access_token=token,
        user=VendorUserResponse.model_validate(vu),
    )


# ── POST /auth/vendor/register (INFERRED-002) ───────────────────────────────

@router.post("/vendor/register", response_model=VendorRegisterResponse, status_code=201)
def vendor_register(body: VendorRegisterRequest, db: Session = Depends(get_db)):
    """Vendor user self-registration."""
    if db.query(VendorUser).filter(VendorUser.email == body.email).first():
        raise HTTPException(400, "Email already registered")

    from app.models.vendor import Vendor

    vendor: Vendor | None = None
    if body.vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == body.vendor_id).first()
        if not vendor:
            raise HTTPException(404, "Vendor profile not found")
    else:
        # Create new vendor profile in GHOST state
        vendor = Vendor(
            name=body.company_name or f"{body.full_name}'s Company",
            status="GHOST",
        )
        db.add(vendor)
        db.flush()

    vu = VendorUser(
        vendor_id=vendor.id,
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="VENDOR_REP",
    )
    db.add(vu)
    db.flush()

    track(db, "vendor_registered", actor_id=vu.id, resource_type="vendor_user", resource_id=vu.id)
    db.commit()

    token = create_access_token({
        "sub": vu.id,
        "email": vu.email,
        "type": "vendor",
        "vendor_id": vendor.id,
        "role": vu.role,
    })

    return VendorRegisterResponse(
        access_token=token,
        vendor_user_id=vu.id,
        vendor_id=vendor.id,
    )