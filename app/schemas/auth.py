"""
auth.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Authentication & Session Schema Layer

CONTRACT AUTHORITY: contract.md §4.1 (Auth Endpoints) + §2.80–2.82
(OAuthLink, RefreshToken, MFAEnrollment) + §1.2 (Repo C exclusively owns auth)

Rules:
  • JWT access tokens: 15-minute TTL, stored IN MEMORY only by Repo A.
  • Refresh tokens:  7-day TTL, httpOnly Secure SameSite=Lax cookie ONLY.
  • MFA methods: totp | sms | webauthn.
  • OAuth providers: google | linkedin | microsoft | saml.
  • Repo B NEVER receives user identity beyond a service-to-service token.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from .common import (
    UserPrioritySensitivity,
    CurrencyCode,
    CountryCode,
    MFAMethod,
    OAuthProvider,
    OrganizationBillingPlan,
    PGIBase,
    UserRole,
    UserStatus,
)


# ──────────────────────────────────────────────────────────────────────────
# Embedded user / org snapshots returned in auth responses
# ──────────────────────────────────────────────────────────────────────────

class AuthUserSnapshot(PGIBase):
    """Minimal user fields embedded in auth responses (contract §4.1 GET /me)."""

    user_id: UUID
    name: str
    email: str
    role: UserRole
    status: UserStatus
    organization_id: UUID
    locale: str
    currency_preference: CurrencyCode
    timezone: str
    approval_level: int
    mfa_enrolled: bool
    priority_sensitivity: Optional[UserPrioritySensitivity] = None
    department: Optional[str] = None
    cost_center: Optional[str] = None
    detected_country: Optional[CountryCode] = None
    detected_currency: Optional[CurrencyCode] = None
    last_active_at: Optional[datetime] = None


class AuthOrganizationSnapshot(PGIBase):
    """Minimal organization fields embedded in auth responses."""

    organization_id: UUID
    name: str
    industry: Optional[str] = None
    default_country: CountryCode
    default_currency: CurrencyCode
    billing_plan: OrganizationBillingPlan
    approval_chain_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/auth/oauth/{provider}/callback
# ──────────────────────────────────────────────────────────────────────────

class OAuthCallbackRequest(PGIBase):
    """OAuth provider callback body sent from Repo A to Repo C.

    guest_session_id is passed when an unauthenticated user signs in after
    performing a guest intelligence-report search — triggers guest merge.
    """

    code: str = Field(description="Authorization code from the OAuth provider.")
    state: str = Field(description="CSRF state token; Repo C validates this.")
    redirect_uri: str = Field(description="Must match the registered URI for the provider.")
    guest_session_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Guest session to merge into the new user session after sign-in. "
            "Null when the user navigated directly to the login page."
        ),
    )


class OAuthCallbackResponse(PGIBase):
    """Auth response after successful OAuth code exchange.

    access_token: short-lived JWT (15 min); Repo A stores IN MEMORY only.
    refresh_token:  returned as httpOnly Secure cookie — NOT in this body.
    """

    access_token: str
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(
        default=900,
        description="Access token TTL in seconds (always 900 = 15 minutes).",
    )
    user: AuthUserSnapshot
    organization: AuthOrganizationSnapshot


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/auth/refresh
# ──────────────────────────────────────────────────────────────────────────

class TokenRefreshRequest(PGIBase):
    """Refresh token exchange.  The refresh_token is read from the httpOnly
    cookie automatically by the browser; no body field needed.
    """

    pass  # body is empty; cookie carries the refresh token


class TokenRefreshResponse(PGIBase):
    """New access token issued after a valid refresh token cookie is presented."""

    access_token: str
    expires_in: int = Field(default=900)


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/auth/logout
# ──────────────────────────────────────────────────────────────────────────

class LogoutRequest(PGIBase):
    """Logout body is empty. Repo C revokes the refresh token and clears cookie."""

    pass


# Response is HTTP 204 — no body schema.


# ──────────────────────────────────────────────────────────────────────────
# GET /api/v1/auth/me
# ──────────────────────────────────────────────────────────────────────────

class MeResponse(PGIBase):
    """Full authenticated-user profile response.

    roles: list of all roles the user holds across the organization.
    plan:  shorthand billing plan string for the user's organization.
    """

    user: AuthUserSnapshot
    organization: AuthOrganizationSnapshot
    roles: list[str]
    plan: OrganizationBillingPlan


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/auth/mfa/enroll
# ──────────────────────────────────────────────────────────────────────────

class MFAEnrollRequest(PGIBase):
    """Initiate MFA enrollment for the authenticated user."""

    method: MFAMethod


class MFAEnrollResponse(PGIBase):
    """MFA enrollment setup payload.

    setup_payload contents vary by method:
      totp:     { "otpauth_uri": str, "secret": str }
      sms:      { "phone_number_last4": str }
      webauthn: { "challenge": str, "rp_id": str }
    """

    enrollment_id: UUID
    setup_payload: dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/auth/mfa/verify
# ──────────────────────────────────────────────────────────────────────────

class MFAVerifyRequest(PGIBase):
    """Submit the MFA verification code for the given enrollment."""

    enrollment_id: UUID
    code: str = Field(min_length=6, max_length=128)


class MFAVerifyResponse(PGIBase):
    """Confirmation that MFA verification succeeded."""

    verified: bool = True


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/sessions/merge-guest
# ──────────────────────────────────────────────────────────────────────────

class MergeGuestRequest(PGIBase):
    """Merge a guest session into the newly authenticated user's workspace.

    Called immediately after OAuthCallbackResponse is processed by Repo A
    if guest_session_id was present in the original callback.
    """

    guest_session_id: UUID = Field(
        description=(
            "ID of the guest session whose search logs should be merged "
            "into a new session-tier project for the authenticated user."
        )
    )


class MergeGuestResponse(PGIBase):
    """Result of guest-to-authenticated merge.

    project_id: the newly created session-tier Project that contains
                the merged Guest_Search_Log entries.
    """

    project_id: UUID


# ──────────────────────────────────────────────────────────────────────────
# OAuth Link (entity schema — contract §2.80)
# ──────────────────────────────────────────────────────────────────────────

class OAuthLinkSchema(PGIBase):
    """Represents a linked OAuth provider on a user account."""

    link_id: UUID
    user_id: UUID
    provider: OAuthProvider
    provider_subject: str
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# RefreshToken (entity schema — contract §2.81)
# ──────────────────────────────────────────────────────────────────────────

class RefreshTokenSchema(PGIBase):
    """Internal representation of an issued refresh token.

    token_hash: SHA-256 of the raw token value — raw value is NEVER stored.
    ip_hash:    SHA-256 of the client IP — raw IP never persisted.
    """

    token_id: UUID
    user_id: UUID
    token_hash: str = Field(min_length=64, max_length=64)
    issued_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = Field(
        default=None, min_length=64, max_length=64
    )


# ──────────────────────────────────────────────────────────────────────────
# MFAEnrollment (entity schema — contract §2.82)
# ──────────────────────────────────────────────────────────────────────────

class MFAEnrollmentSchema(PGIBase):
    """Stored MFA enrollment record.

    secret_encrypted: BYTEA in DB — exposed here as base64 string.
    Only returned to privileged internal callers; never surfaced via
    public API in plaintext.
    """

    enrollment_id: UUID
    user_id: UUID
    method: MFAMethod
    enrolled_at: datetime
    disabled_at: Optional[datetime] = None
