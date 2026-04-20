"""
Project, ProjectACL, WorkspaceDecision, BOMUpload, BOMLine entities.

Contract anchors
----------------
§2.4  Project        §2.5  ProjectACL      §2.91 WorkspaceDecision
§2.6  BOM_Upload     §2.7  BOM_Line
§3.1  BOMLine.status (SM-001)     §3.2 Project.state (SM-002)
§3.3  Project.is_session_or_project (SM-003)
§3.4  BOMUpload.import_status
§3.37 BOMUpload.source_type       §3.46 Project.weight_profile
§3.47 Project.stage               §3.48 Priority
§3.72 BOMLine.sourcing_type       §3.87 ProjectACL.role
§3.89 WorkspaceDecision.from_state/to_state

Notes
-----
* CN-11: ``project.state`` is required (SM-002).
* CN-3: BOM_Line uses the 17-state SM-001 vocabulary.
* CN-20: ``bom_line.score_cache_json`` is kept as denormalized read cache;
  authoritative scoring data lives in ``vendor_score_cache`` (see
  ``intelligence.py``).
* ``bom_line.raw_text`` is immutable after insert — enforced by service layer
  + audit trail (trigger enforcement deferred to migrations).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Interval,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TimestampMixin,
    country_code_nullable,
    enum_check,
    jsonb_array,
    jsonb_object,
    jsonb_object_nullable,
    money_default_zero,
    nullable_enum_check,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    BOMLineStatus,
    BOMUploadImportStatus,
    BOMUploadSourceType,
    Priority,
    ProjectACLRole,
    ProjectSessionType,
    ProjectStage,
    ProjectState,
    ProjectWeightProfile,
    SourcingMode,
    WorkspaceDecisionStateValue,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# Project (§2.4)
# ─────────────────────────────────────────────────────────────────────────────


class Project(Base, TimestampMixin, SoftDeleteMixin):
    """A buyer's sourcing workspace — starts as ``session`` and may be
    promoted to ``project`` (one-way)."""

    __tablename__ = "project"

    project_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    created_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT", index=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_country: Mapped[str | None] = country_code_nullable()
    target_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    delivery_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    target_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NORMAL'")
    )
    stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    weight_profile: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'balanced'")
    )
    weight_profile_custom_json: Mapped[dict | None] = jsonb_object_nullable()
    is_session_or_project: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'session'")
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'DRAFT'")
    )
    required_timeline: Mapped = mapped_column(Interval, nullable=True)
    incoterm_preference: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Relationships
    bom_uploads: Mapped[list["BOMUpload"]] = relationship(
        "BOMUpload",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    bom_lines: Mapped[list["BOMLine"]] = relationship(
        "BOMLine",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    acls: Mapped[list["ProjectACL"]] = relationship(
        "ProjectACL",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    # is_session_or_project may only transition session -> project; reverse
    # transition is application-enforced, never DB-driven.
    __table_args__ = (
        enum_check("priority", values_of(Priority)),
        nullable_enum_check("stage", values_of(ProjectStage)),
        enum_check("weight_profile", values_of(ProjectWeightProfile)),
        enum_check("is_session_or_project", values_of(ProjectSessionType)),
        enum_check("state", values_of(ProjectState)),
        # Custom weights required iff weight_profile='custom'
        CheckConstraint(
            "(weight_profile <> 'custom') OR (weight_profile_custom_json IS NOT NULL)",
            name="weight_profile_custom_json_required",
        ),
        # Project name required when promoted to a full project
        CheckConstraint(
            "(is_session_or_project = 'session') OR (name IS NOT NULL)",
            name="project_name_required_when_project",
        ),
        CheckConstraint(
            "(is_session_or_project = 'session') OR (stage IS NOT NULL)",
            name="project_stage_required_when_project",
        ),
        Index("ix_project_organization_id_state", "organization_id", "state"),
        Index("ix_project_is_session_or_project", "is_session_or_project"),
        Index(
            "ix_project_organization_id_is_session_or_project",
            "organization_id",
            "is_session_or_project",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ProjectACL (§2.5)
# ─────────────────────────────────────────────────────────────────────────────


class ProjectACL(Base, CreatedAtMixin):
    """Per-project role assignment (owner / viewer / approver / editor)."""

    __tablename__ = "project_acl"

    acl_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT", index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = tstz(default_now=True)
    granted_by: Mapped[uuid.UUID] = uuid_fk("user.user_id", ondelete="RESTRICT")

    project: Mapped["Project"] = relationship(
        "Project", back_populates="acls", lazy="raise"
    )

    __table_args__ = (
        enum_check("role", values_of(ProjectACLRole)),
        UniqueConstraint("project_id", "user_id", name="uq_project_acl_project_id_user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# WorkspaceDecision (§2.91)  — append-only log of session↔project transitions
# ─────────────────────────────────────────────────────────────────────────────


class WorkspaceDecision(Base, CreatedAtMixin):
    """Append-only record of workspace-type transitions.

    Invariant: ``from_state='session'`` and ``to_state='project'`` only
    (one-way promotion). Reverse transitions are prohibited — enforced in
    :mod:`app.services.project_service`.
    """

    __tablename__ = "workspace_decision"

    decision_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    decided_by: Mapped[uuid.UUID] = uuid_fk("user.user_id", ondelete="RESTRICT")
    from_state: Mapped[str] = mapped_column(String(8), nullable=False)
    to_state: Mapped[str] = mapped_column(String(8), nullable=False)
    decided_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("from_state", values_of(WorkspaceDecisionStateValue)),
        enum_check("to_state", values_of(WorkspaceDecisionStateValue)),
        CheckConstraint(
            "from_state <> to_state",
            name="workspace_decision_state_change",
        ),
        CheckConstraint(
            "from_state = 'session' AND to_state = 'project'",
            name="workspace_decision_session_to_project_only",
        ),
        Index("ix_workspace_decision_project_id", "project_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BOMUpload (§2.6)
# ─────────────────────────────────────────────────────────────────────────────


class BOMUpload(Base, CreatedAtMixin):
    """Raw BOM file / typed-entry upload batch."""

    __tablename__ = "bom_upload"

    upload_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RECEIVED'")
    )
    validation_errors_json: Mapped[list] = jsonb_array()
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    column_mapping_json: Mapped[dict] = jsonb_object()
    processed_at: Mapped[datetime | None] = tstz(nullable=True)

    project: Mapped["Project"] = relationship(
        "Project", back_populates="bom_uploads", lazy="raise"
    )

    __table_args__ = (
        enum_check("source_type", values_of(BOMUploadSourceType)),
        enum_check("import_status", values_of(BOMUploadImportStatus)),
        CheckConstraint("row_count >= 0", name="row_count_nonneg"),
        CheckConstraint(
            "file_hash IS NULL OR char_length(file_hash) = 64",
            name="file_hash_sha256_hex",
        ),
        UniqueConstraint("project_id", "file_hash", name="uq_bom_upload_project_id_file_hash"),
        Index("ix_bom_upload_import_status", "import_status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BOMLine (§2.7)
# ─────────────────────────────────────────────────────────────────────────────


class BOMLine(Base, TimestampMixin):
    """Single component / line-item within a project.

    * ``raw_text`` is immutable after insert.
    * ``status`` transitions only via the workflow orchestrator (SM-001).
    * ``score_cache_json`` is a denormalized read-cache — authoritative
      scores are in ``vendor_score_cache`` (CN-20).
    """

    __tablename__ = "bom_line"

    bom_line_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    upload_id: Mapped[uuid.UUID | None] = uuid_fk(
        "bom_upload.upload_id", ondelete="SET NULL", nullable=True
    )
    part_id: Mapped[uuid.UUID | None] = uuid_fk(
        "part_master.part_id", ondelete="SET NULL", nullable=True
    )
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spec_json: Mapped[dict] = jsonb_object()
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default=text("0")
    )
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_country: Mapped[str | None] = country_code_nullable()
    delivery_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NORMAL'")
    )
    acceptable_substitutes: Mapped[list] = jsonb_array()
    required_certifications: Mapped[list] = jsonb_array()
    manufacturer_part_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RAW'")
    )
    sourcing_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    normalization_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    enrichment_json: Mapped[dict] = jsonb_object()
    # CN-20: denormalized read cache only. Authoritative data = vendor_score_cache.
    score_cache_json: Mapped[dict] = jsonb_object()

    project: Mapped["Project"] = relationship(
        "Project", back_populates="bom_lines", lazy="raise"
    )

    __table_args__ = (
        enum_check("priority", values_of(Priority)),
        enum_check("status", values_of(BOMLineStatus)),
        nullable_enum_check("sourcing_type", values_of(SourcingMode)),
        # Quantity must be positive on any non-DRAFT (RAW) state per §2.7.
        # RAW is the ingestion-time state where the value may be 0 until
        # the user confirms; all downstream states require qty > 0.
        CheckConstraint(
            "status = 'RAW' OR quantity > 0",
            name="quantity_positive_on_active_states",
        ),
        CheckConstraint(
            "normalization_confidence IS NULL "
            "OR (normalization_confidence >= 0 AND normalization_confidence <= 1)",
            name="normalization_confidence_range",
        ),
        Index("ix_bom_line_project_id_status", "project_id", "status"),
        Index("ix_bom_line_part_id", "part_id"),
        Index("ix_bom_line_upload_id", "upload_id"),
        Index("ix_bom_line_spec_json", "spec_json", postgresql_using="gin"),
        Index("ix_bom_line_enrichment_json_gin", "enrichment_json", postgresql_using="gin"),
    )


__all__ = [
    "Project",
    "ProjectACL",
    "WorkspaceDecision",
    "BOMUpload",
    "BOMLine",
]
