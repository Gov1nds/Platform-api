"""
User, Organization, and session models.

References: GAP-005, GAP-008, GAP-030, canonical-domain-model.md BC-01/BC-02,
            state-machines.md, roles-permissions.yaml ACL-001
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, Index,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# ── Organization (ENT-001) ───────────────────────────────────────────────────

class Organization(Base):
    """Multi-tenant organization. All business entities are scoped here."""
    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_org_slug", "slug", unique=True),
        {"schema": "auth"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name = Column(Text, nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    type = Column(String(40), nullable=False, default="buyer")  # buyer, vendor, platform
    settings_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    memberships = relationship(
        "OrganizationMembership", back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMembership(Base):
    """Links users to organizations with a role."""
    __tablename__ = "organization_memberships"
    __table_args__ = (
        Index("ix_orgmem_org", "organization_id"),
        Index("ix_orgmem_user", "user_id"),
        UniqueConstraint("organization_id", "user_id", name="uq_orgmem_org_user"),
        {"schema": "auth"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(40), nullable=False, default="BUYER_VIEWER")
    invited_by = Column(UUID(as_uuid=False), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="memberships")
    user = relationship("User", back_populates="memberships")


# ── User (ENT-002) ──────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_org", "organization_id"),
        {"schema": "auth"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    email = Column(String(320), nullable=False, unique=True)
    password_hash = Column(Text, nullable=True)  # nullable for OAuth-only users
    full_name = Column(Text, nullable=False, default="")
    role = Column(String(40), nullable=False, default="BUYER_EDITOR")
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    permissions = Column(JSONB, nullable=False, default=list)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    # OAuth fields (GAP-008)
    oauth_provider = Column(String(40), nullable=True)
    oauth_provider_id = Column(String(320), nullable=True)

    # MFA (GAP-008)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa_secret = Column(Text, nullable=True)

    # Lifecycle
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    projects = relationship("Project", back_populates="user")
    boms = relationship("BOM", back_populates="user")
    rfqs = relationship("RFQBatch", back_populates="user")
    memberships = relationship(
        "OrganizationMembership", back_populates="user", cascade="all, delete-orphan"
    )


# ── Guest Session (ENT-006) ─────────────────────────────────────────────────

class GuestSession(Base):
    __tablename__ = "guest_sessions"
    __table_args__ = (
        Index("ix_guest_sessions_token", "session_token", unique=True),
        Index("ix_guest_status_expires", "status", "expires_at"),
        {"schema": "auth"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    session_token = Column(String(120), nullable=False, unique=True)
    status = Column(String(40), nullable=False, default="ACTIVE")  # GuestSessionStatus
    merged_user_id = Column(UUID(as_uuid=False), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    # Lifecycle fields (GAP-030)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Geolocation / rate limiting
    detected_location = Column(Text, nullable=True)
    detected_currency = Column(String(3), nullable=True)
    ip_address = Column(String(45), nullable=True)
    component_count = Column(Integer, nullable=False, default=0)
    search_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


# ── Vendor User ──────────────────────────────────────────────────────────────

class VendorUser(Base):
    __tablename__ = "vendor_users"
    __table_args__ = (
        Index("ix_vendor_users_email", "email", unique=True),
        {"schema": "auth"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(UUID(as_uuid=False), nullable=False, index=True)
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    email = Column(String(320), nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False, default="")
    role = Column(String(40), nullable=False, default="VENDOR_REP")  # VendorRole
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)