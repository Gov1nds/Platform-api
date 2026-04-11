"""
Project, session, and workspace models.

References: GAP-004 (SM-002, SM-003), GAP-005, GAP-014, GAP-025,
            architecture.md Domain 6, state-machines.md FSD-02/FSD-03,
            canonical-domain-model.md BC-03
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Integer, DateTime, ForeignKey, Numeric, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Project(Base):
    """
    Project entity (ENT-009).

    Status follows ProjectStatus enum (SM-002, 12 states):
    DRAFT → INTAKE_COMPLETE → ANALYSIS_IN_PROGRESS → … → CLOSED | CANCELLED | ARCHIVED
    """
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_guest_session", "guest_session_id"),
        Index("ix_projects_org", "organization_id"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    guest_session_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    sourcing_case_id = Column(UUID(as_uuid=False), nullable=True)
    name = Column(Text, nullable=False, default="Uploaded BOM")
    file_name = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="DRAFT")  # ProjectStatus (SM-002)
    visibility = Column(Text, nullable=False, default="owner_only")
    weight_profile = Column(String(40), nullable=False, default="balanced")  # PC-004

    # Denormalized counters
    total_parts = Column(Integer, nullable=False, default=0)
    bom_upload_count = Column(Integer, nullable=False, default=0)
    bom_line_count = Column(Integer, nullable=False, default=0)
    rfq_count = Column(Integer, nullable=False, default=0)
    po_count = Column(Integer, nullable=False, default=0)

    # Cost fields — Numeric(20,8) per GAP-025
    average_cost = Column(Numeric(20, 8), nullable=True)
    cost_range_low = Column(Numeric(20, 8), nullable=True)
    cost_range_high = Column(Numeric(20, 8), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    decision_summary = Column(Text, nullable=True)
    current_rfq_id = Column(UUID(as_uuid=False), nullable=True)
    current_po_id = Column(UUID(as_uuid=False), nullable=True)
    analyzer_report = Column(JSONB, nullable=False, default=dict)
    strategy = Column(JSONB, nullable=False, default=dict)
    project_metadata = Column(JSONB, nullable=False, default=dict)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="projects")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")
    acl_entries = relationship("ProjectACL", back_populates="project", cascade="all, delete-orphan")


class ProjectACL(Base):
    __tablename__ = "project_acl"
    __table_args__ = (
        Index("ix_pacl_project", "project_id"),
        Index("ix_pacl_principal", "principal_type", "principal_id"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    principal_type = Column(Text, nullable=False)
    principal_id = Column(UUID(as_uuid=False), nullable=False)
    role = Column(Text, nullable=False, default="viewer")
    granted_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="acl_entries")


class ProjectEvent(Base):
    """Legacy project event tracking. New audit goes to EventAuditLog."""
    __tablename__ = "project_events"
    __table_args__ = {"schema": "projects"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type = Column(Text, nullable=False)
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    actor_user_id = Column(UUID(as_uuid=False), nullable=True)
    trace_id = Column(String(64), nullable=True)
    idempotency_key = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    project = relationship("Project", back_populates="events")


class SearchSession(Base):
    """
    Sourcing Session entity (ENT-008, SM-003).

    Maps to Sourcing_Session in the canonical model.
    """
    __tablename__ = "search_sessions"
    __table_args__ = (
        Index("ix_ss_user", "user_id"),
        Index("ix_ss_guest", "guest_session_id"),
        Index("ix_ss_org", "organization_id"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    session_token = Column(String(120), nullable=True)
    query_text = Column(Text, nullable=True)
    query_type = Column(String(40), nullable=False, default="component")
    input_type = Column(String(40), nullable=False, default="text")
    delivery_location = Column(Text, nullable=True)
    target_currency = Column(String(10), nullable=True, default="USD")
    results_json = Column(JSONB, nullable=False, default=dict)
    analysis_payload = Column(JSONB, nullable=False, default=dict)
    promoted_to = Column(String(40), nullable=True)
    promoted_to_id = Column(UUID(as_uuid=False), nullable=True)
    status = Column(String(40), nullable=False, default="ACTIVE")  # SessionStatus (SM-003)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class SourcingCase(Base):
    __tablename__ = "sourcing_cases"
    __table_args__ = (
        Index("ix_sc_user", "user_id"),
        Index("ix_sc_guest", "guest_session_id"),
        Index("ix_sc_org", "organization_id"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    session_token = Column(String(120), nullable=True)
    search_session_id = Column(UUID(as_uuid=False), nullable=True)
    name = Column(Text, nullable=False, default="Saved search")
    query_text = Column(Text, nullable=True)
    analysis_payload = Column(JSONB, nullable=False, default=dict)
    vendor_shortlist = Column(JSONB, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    promoted_to_project_id = Column(UUID(as_uuid=False), nullable=True)
    status = Column(String(40), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


# IntakeSession and IntakeItem removed per APPROVED-ENHANCEMENT-002 (dead code).