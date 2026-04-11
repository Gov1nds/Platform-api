"""
Auth-specific Pydantic schemas.

Covers token exchange (OAuth), refresh, guest conversion,
dashboard hydration, and vendor registration.

References: GAP-008, api-contract-review.md Section 5.3,
            frontend-backend-contract.md
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Token exchange (OAuth / password) ────────────────────────────────────────

class TokenExchangeRequest(BaseModel):
    """POST /auth/token — exchange credentials for access + refresh tokens."""
    grant_type: str = "authorization_code"  # authorization_code | password
    provider: str | None = None  # google, linkedin, microsoft, saml
    code: str | None = None  # OAuth authorization code
    redirect_uri: str | None = None
    # Password fallback (dev/transition)
    email: str | None = None
    password: str | None = None
    # Guest merge
    guest_session_id: str | None = None


class TokenExchangeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900  # seconds
    user: AuthUserResponse


class AuthUserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    organization_id: str | None = None
    is_active: bool = True
    is_verified: bool = False

    model_config = {"from_attributes": True}


# ── Refresh ──────────────────────────────────────────────────────────────────

class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900


# ── Guest conversion ────────────────────────────────────────────────────────

class GuestConvertRequest(BaseModel):
    """POST /auth/convert-guest — same-page guest → auth conversion."""
    email: str
    password: str
    full_name: str = ""


class GuestConvertResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    user: AuthUserResponse
    merge_result: dict = {}


# ── Vendor registration ─────────────────────────────────────────────────────

class VendorRegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    vendor_id: str | None = None  # claim existing vendor profile
    company_name: str | None = None  # or create new


class VendorRegisterResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    vendor_user_id: str
    vendor_id: str


# ── Dashboard hydration ─────────────────────────────────────────────────────

class DashboardKPIs(BaseModel):
    active_projects: int = 0
    pending_rfqs: int = 0
    open_pos: int = 0
    pending_approvals: int = 0


class DashboardResponse(BaseModel):
    user: AuthUserResponse
    organization: dict | None = None
    kpis: DashboardKPIs = Field(default_factory=DashboardKPIs)
    action_queue: list[dict] = []
    unread_notifications: int = 0