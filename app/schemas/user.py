"""
user.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — User & Organization Schema Layer

CONTRACT AUTHORITY: contract.md §2.2 (User), §2.3 (Organization),
§2.83 (OrganizationMembership) + requirements.yaml domains/identity_and_access.

Invariants encoded here:
  • email must be stored lowercase (contract §2.2 UNIQUE, lowercase CHECK).
  • currency_preference must be exactly 3 uppercase chars (ISO-4217).
  • detected_country must be exactly 2 uppercase chars (ISO-3166 alpha-2).
  • approval_level >= 0.
  • deleted_at (soft-delete) is nullable; presence means GDPR-erased/deleted.
  • Versioning: soft_delete_with_pii_anonymization_on_gdpr.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from .common import (
    Money,
    CountryCode,
    CurrencyCode,
    OrganizationBillingPlan,
    OrganizationMembershipRole,
    PGIBase,
    TIMESTAMPTZ,
    UserPrioritySensitivity,
    UserRole,
    UserStatus,
)


# ──────────────────────────────────────────────────────────────────────────
# User
# ──────────────────────────────────────────────────────────────────────────

class UserResponse(PGIBase):
    """Full user entity — returned by GET /api/v1/auth/me and admin endpoints."""

    user_id: UUID
    organization_id: UUID
    name: str
    email: str
    role: UserRole
    status: UserStatus
    locale: str = Field(default="en-US")
    currency_preference: CurrencyCode = Field(default="USD")
    timezone: str = Field(default="UTC")
    approval_level: int = Field(default=0, ge=0)
    department: Optional[str] = None
    cost_center: Optional[str] = None
    detected_country: Optional[CountryCode] = None
    detected_currency: Optional[CurrencyCode] = None
    priority_sensitivity: Optional[UserPrioritySensitivity] = None
    mfa_enrolled: bool = False
    last_active_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    @field_validator("email", mode="before")
    @classmethod
    def email_must_be_lowercase(cls, v: str) -> str:
        return v.strip().lower()


class UserCreateRequest(PGIBase):
    """Internal schema for creating a user record after OAuth verification.

    Not exposed as a public endpoint — Repo C creates users internally
    during the OAuth callback flow.
    """

    organization_id: UUID
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    role: UserRole = Field(default=UserRole.BUYER_VIEWER)
    locale: str = Field(default="en-US", max_length=16)
    currency_preference: CurrencyCode = Field(default="USD")
    timezone: str = Field(default="UTC", max_length=64)
    detected_country: Optional[CountryCode] = None
    detected_currency: Optional[CurrencyCode] = None
    priority_sensitivity: Optional[UserPrioritySensitivity] = None

    @field_validator("email", mode="before")
    @classmethod
    def email_must_be_lowercase(cls, v: str) -> str:
        return v.strip().lower()


class UserUpdateRequest(PGIBase):
    """Fields a user may update on their own profile via PATCH."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    locale: Optional[str] = Field(default=None, max_length=16)
    currency_preference: Optional[CurrencyCode] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    department: Optional[str] = Field(default=None, max_length=128)
    cost_center: Optional[str] = Field(default=None, max_length=64)
    priority_sensitivity: Optional[UserPrioritySensitivity] = None
    detected_country: Optional[CountryCode] = None
    detected_currency: Optional[CurrencyCode] = None


class UserAdminUpdateRequest(PGIBase):
    """Fields an admin/owner may update on another user's account."""

    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None
    approval_level: Optional[int] = Field(default=None, ge=0)
    department: Optional[str] = Field(default=None, max_length=128)
    cost_center: Optional[str] = Field(default=None, max_length=64)


# ──────────────────────────────────────────────────────────────────────────
# Organization
# ──────────────────────────────────────────────────────────────────────────

class OrganizationResponse(PGIBase):
    """Full organization entity."""

    organization_id: UUID
    name: str
    industry: Optional[str] = None
    default_country: CountryCode
    default_currency: CurrencyCode
    billing_plan: OrganizationBillingPlan
    compliance_profile: dict[str, Any] = Field(default_factory=dict)
    auto_order_threshold: Money
    preferred_vendor_list_id: Optional[UUID] = None
    report_cadence: dict[str, Any] = Field(default_factory=dict)
    approval_chain_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class BuyerFacingOrganizationResponse(OrganizationResponse):
    """Full organization projection for users in the buyer organization."""


class VendorFacingOrganizationResponse(PGIBase):
    """Vendor-safe buyer organization projection.

    Excludes billing plan, approval chain, compliance profile, and
    auto-order threshold so buyer budget and internal controls are not exposed.
    """

    organization_id: UUID
    name: str
    industry: Optional[str] = None
    default_country: CountryCode
    default_currency: CurrencyCode


class OrganizationCreateRequest(PGIBase):
    """Create a new organization (called during first OAuth sign-up)."""

    name: str = Field(min_length=1, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=128)
    default_country: CountryCode
    default_currency: CurrencyCode = Field(default="USD")
    billing_plan: OrganizationBillingPlan = Field(default=OrganizationBillingPlan.FREE)
    compliance_profile: dict[str, Any] = Field(default_factory=dict)
    approval_chain_json: dict[str, Any] = Field(default_factory=dict)


class OrganizationUpdateRequest(PGIBase):
    """Fields an organization owner/admin may update."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=128)
    default_country: Optional[CountryCode] = None
    default_currency: Optional[CurrencyCode] = None
    compliance_profile: Optional[dict[str, Any]] = None
    auto_order_threshold: Optional[Money] = None
    preferred_vendor_list_id: Optional[UUID] = None
    report_cadence: Optional[dict[str, Any]] = None
    approval_chain_json: Optional[dict[str, Any]] = None


class OrganizationPlanChangeRequest(PGIBase):
    """Admin-initiated plan upgrade / downgrade.

    Emits organization.plan_changed event which triggers notification
    and analytics consumers.
    """

    billing_plan: OrganizationBillingPlan
    effective_at: Optional[TIMESTAMPTZ] = Field(
        default=None,
        description="When null, effective immediately.",
    )


# ──────────────────────────────────────────────────────────────────────────
# OrganizationMembership (contract §2.83)
# ──────────────────────────────────────────────────────────────────────────

class OrganizationMembershipSchema(PGIBase):
    """Links a user to an organization with a role.

    Composite PK in DB: (organization_id, user_id).
    """

    organization_id: UUID
    user_id: UUID
    role: OrganizationMembershipRole
    joined_at: datetime


class AddMemberRequest(PGIBase):
    """Invite / add a user to an organization with an initial role."""

    user_id: UUID
    role: OrganizationMembershipRole = Field(
        default=OrganizationMembershipRole.BUYER_VIEWER
    )


class UpdateMemberRoleRequest(PGIBase):
    """Change an existing member's role within the organization."""

    role: OrganizationMembershipRole


# ──────────────────────────────────────────────────────────────────────────
# Paginated user list
# ──────────────────────────────────────────────────────────────────────────

class UserListResponse(PGIBase):
    """Cursor-paginated list of users within an organization."""

    items: list[UserResponse]
    next_cursor: Optional[str] = None
