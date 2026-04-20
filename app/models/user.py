"""
Identity, organization, auth-session, and vendor-user entities.

Contract anchors
----------------
§2.2  User              §2.3  Organization           §2.14 VendorUser
§2.80 OAuthLink         §2.81 RefreshToken           §2.82 MFAEnrollment
§2.83 OrganizationMembership
§3.21 User.status       §3.22 VendorUser.status      §3.23 Organization.billing_plan
§3.24 User.role         §3.64 User.priority_sensitivity
§3.82 OAuthLink.provider §3.83 MFAEnrollment.method  §3.88 VendorUser.role

Notes
-----
* ``VendorUser`` is defined here (per Repo C file map) and referenced by FK
  from ``app/models/vendor.py``.
* ``Organization.preferred_vendor_list_id`` is a cyclic FK back to
  ``preferred_vendor_list``; emitted via ALTER TABLE (``use_alter=True``) to
  avoid circular CREATE TABLE ordering.
* CN-1: authenticated refresh tokens expire in 7 days (policy enforced in
  ``auth_service``); guest session cookies are 30-day sliding (modelled in
  ``guest.py``). This file only persists token metadata.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TimestampMixin,
    enum_check,
    jsonb_object,
    money_default_zero,
    nullable_enum_check,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    MFAMethod,
    OAuthProvider,
    OrganizationBillingPlan,
    OrganizationMembershipRole,
    UserPrioritySensitivity,
    UserRole,
    UserStatus,
    VendorUserRole,
    VendorUserStatus,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# Organization (§2.3)
# ─────────────────────────────────────────────────────────────────────────────


class Organization(Base, TimestampMixin, SoftDeleteMixin):
    """Multi-tenant organization. Every buyer-side row in the system is
    scoped by ``organization_id``."""

    __tablename__ = "organization"

    organization_id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_country: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'US'")
    )
    default_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    billing_plan: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'FREE'")
    )
    compliance_profile: Mapped[dict] = jsonb_object()
    auto_order_threshold: Mapped = money_default_zero()
    preferred_vendor_list_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "preferred_vendor_list.list_id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_organization_preferred_vendor_list_id",
        ),
        nullable=True,
    )
    report_cadence: Mapped[dict] = jsonb_object()
    approval_chain_json: Mapped[dict] = jsonb_object()

    # Relationships
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="organization", lazy="raise"
    )
    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        enum_check("billing_plan", values_of(OrganizationBillingPlan)),
        CheckConstraint("char_length(default_country) = 2", name="default_country_iso3166"),
        CheckConstraint("char_length(default_currency) = 3", name="default_currency_iso4217"),
        CheckConstraint("auto_order_threshold >= 0", name="auto_order_threshold_nonneg"),
        Index("ix_organization_billing_plan", "billing_plan"),
        Index("ix_organization_default_country", "default_country"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# User (§2.2)
# ─────────────────────────────────────────────────────────────────────────────


class User(Base, TimestampMixin, SoftDeleteMixin):
    """Authenticated buyer-side user. Vendor-side identities live in
    :class:`VendorUser`."""

    __tablename__ = "user"

    user_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'buyer_viewer'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PENDING_VERIFICATION'")
    )
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'en-US'")
    )
    currency_preference: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'UTC'")
    )
    approval_level: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cost_center: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    detected_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    priority_sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mfa_enrolled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_active_at: Mapped[datetime | None] = tstz(nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="users", lazy="raise"
    )
    oauth_links: Mapped[list["OAuthLink"]] = relationship(
        "OAuthLink", back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    mfa_enrollments: Mapped[list["MFAEnrollment"]] = relationship(
        "MFAEnrollment",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("char_length(currency_preference) = 3", name="currency_preference_iso4217"),
        CheckConstraint("approval_level >= 0", name="approval_level_nonneg"),
        enum_check("role", values_of(UserRole)),
        enum_check("status", values_of(UserStatus)),
        nullable_enum_check("priority_sensitivity", values_of(UserPrioritySensitivity)),
        Index("ix_user_organization_id", "organization_id"),
        Index("ix_user_status", "status"),
        Index("ix_user_last_active_at", "last_active_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OrganizationMembership (§2.83)
# ─────────────────────────────────────────────────────────────────────────────


class OrganizationMembership(Base):
    """Membership link between a user and an organization.

    Separate from ``user.organization_id`` to support future multi-org users
    while preserving a canonical home-org per User. Composite PK.
    """

    __tablename__ = "organization_membership"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organization.organization_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    joined_at: Mapped[datetime] = tstz(default_now=True)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="memberships", lazy="raise"
    )

    __table_args__ = (
        enum_check("role", values_of(OrganizationMembershipRole)),
        Index("ix_organization_membership_user_id", "user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OAuthLink (§2.80)
# ─────────────────────────────────────────────────────────────────────────────


class OAuthLink(Base, CreatedAtMixin):
    """Link between a User and an external OAuth / SAML identity."""

    __tablename__ = "oauth_link"

    link_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="CASCADE", index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(256), nullable=False)

    user: Mapped["User"] = relationship(
        "User", back_populates="oauth_links", lazy="raise"
    )

    __table_args__ = (
        enum_check("provider", values_of(OAuthProvider)),
        UniqueConstraint("provider", "provider_subject", name="uq_oauth_link_provider_subject"),
        Index("ix_oauth_link_user_id", "user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RefreshToken (§2.81)
# ─────────────────────────────────────────────────────────────────────────────


class RefreshToken(Base):
    """Hashed refresh token metadata (we never persist plaintext).

    Rotation: ``auth_service`` rotates on every refresh call (CN-1).
    TTL: 7 days from ``issued_at`` (enforced in service).
    """

    __tablename__ = "refresh_token"

    token_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="CASCADE", index=True
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    issued_at: Mapped[datetime] = tstz(default_now=True)
    expires_at: Mapped[datetime] = tstz()
    revoked_at: Mapped[datetime | None] = tstz(nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)

    user: Mapped["User"] = relationship(
        "User", back_populates="refresh_tokens", lazy="raise"
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_token_token_hash"),
        Index("ix_refresh_token_user_id", "user_id"),
        Index("ix_refresh_token_expires_at", "expires_at"),
    )

# MFAEnrollment (§2.82)


class MFAEnrollment(Base):
    """Per-user MFA factor enrollment. ``secret_encrypted`` is TOTP seed /
    WebAuthn credential encrypted with the platform KMS key."""

    __tablename__ = "mfa_enrollment"

    enrollment_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="CASCADE", index=True
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    enrolled_at: Mapped[datetime] = tstz(default_now=True)
    disabled_at: Mapped[datetime | None] = tstz(nullable=True)

    user: Mapped["User"] = relationship(
        "User", back_populates="mfa_enrollments", lazy="raise"
    )

    __table_args__ = (
        enum_check("method", values_of(MFAMethod)),
        Index("ix_mfa_enrollment_user_id", "user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorUser (§2.14)  — defined here per file map; vendor.py FKs to this.
# ─────────────────────────────────────────────────────────────────────────────


class VendorUser(Base, TimestampMixin, SoftDeleteMixin):
    """Vendor-portal user account (separate auth context from buyer User).

    Vendor users authenticate against the ``/api/v1/vendor/*`` surface only.
    """

    __tablename__ = "vendor_user"

    vendor_user_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id",
        ondelete="CASCADE",
        index=True,
        use_alter=True,
        name="fk_vendor_user_vendor_id",
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'vendor_rep'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING_VERIFICATION'")
    )
    mfa_enrolled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_active_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        UniqueConstraint("vendor_id", "email", name="uq_vendor_user_vendor_id_email"),
        enum_check("role", values_of(VendorUserRole)),
        enum_check("status", values_of(VendorUserStatus)),
        Index("ix_vendor_user_vendor_id", "vendor_id"),
    )


__all__ = [
    "Organization",
    "User",
    "OrganizationMembership",
    "OAuthLink",
    "RefreshToken",
    "MFAEnrollment",
    "VendorUser",
]
