"""Organization, Workspace, and Membership models.

P-3/DB-1: Procurement is team-based. This module provides the org/workspace
tenancy layer so projects, approvals, RFQs, and analytics can be scoped
to workspaces rather than individual users only.

Schema: orgs
Tables: organizations, workspaces, workspace_memberships
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, Text, DateTime, ForeignKey, Boolean, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class OrgStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    archived = "archived"


class MemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    manager = "manager"
    buyer = "buyer"
    sourcing = "sourcing"
    approver = "approver"
    viewer = "viewer"
    vendor = "vendor"


class MemberStatus(str, enum.Enum):
    active = "active"
    invited = "invited"
    deactivated = "deactivated"


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_organizations_status", "status"),
        Index("ix_organizations_slug", "slug", unique=True),
        {"schema": "orgs"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(Text, nullable=False)
    slug = Column(Text, nullable=False, unique=True)
    status = Column(Text, nullable=False, default=OrgStatus.active.value)
    owner_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    # Org-level settings
    default_currency = Column(Text, nullable=False, default="USD")
    default_region = Column(Text, nullable=True)
    logo_url = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    workspaces = relationship("Workspace", back_populates="organization", cascade="all, delete-orphan")
    owner = relationship("User", foreign_keys=[owner_user_id])


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspaces_org", "organization_id"),
        Index("ix_workspaces_status", "status"),
        {"schema": "orgs"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(UUID(as_uuid=False), ForeignKey("orgs.organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default=OrgStatus.active.value)

    # Workspace-level overrides
    default_currency = Column(Text, nullable=True)
    default_region = Column(Text, nullable=True)
    budget_limit = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    organization = relationship("Organization", back_populates="workspaces")
    memberships = relationship("WorkspaceMembership", back_populates="workspace", cascade="all, delete-orphan")


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        Index("ix_ws_memberships_workspace", "workspace_id"),
        Index("ix_ws_memberships_user", "user_id"),
        Index("ix_ws_memberships_status", "status"),
        {"schema": "orgs"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("orgs.workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False)

    role = Column(Text, nullable=False, default=MemberRole.viewer.value)
    status = Column(Text, nullable=False, default=MemberStatus.active.value)
    invited_email = Column(Text, nullable=True)
    invited_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    joined_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="memberships")
    user = relationship("User", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
