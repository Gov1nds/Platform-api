"""
Vendor, capability, match, and performance snapshot models.

References: GAP-013 (MKT-002), GAP-017, GAP-005, state-machines.md MKT-002,
            canonical-domain-model.md BC-08
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, ForeignKey, Numeric,
    Integer, Date, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Vendor(Base):
    """
    Vendor entity.

    Status follows VendorStatus enum (MKT-002):
    GHOST → INVITED → CLAIM_PENDING → BASIC → STANDARD → PREMIUM | SUSPENDED | DEACTIVATED
    """
    __tablename__ = "vendors"
    __table_args__ = (
        Index("ix_vendors_org", "organization_id"),
        Index("ix_vendors_status", "status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name = Column(Text, nullable=False)
    legal_name = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    region = Column(Text, nullable=True)
    website = Column(Text, nullable=True)
    contact_email = Column(Text, nullable=True)
    contact_phone = Column(Text, nullable=True)
    reliability_score = Column(Numeric(12, 6), nullable=False, default=0.8)
    avg_lead_time_days = Column(Numeric(12, 2), nullable=True)
    default_currency = Column(String(3), nullable=False, default="USD")
    default_moq = Column(Numeric(20, 8), nullable=True)
    regions_served = Column(JSONB, nullable=False, default=list)
    certifications = Column(JSONB, nullable=False, default=list)
    capacity_profile = Column(JSONB, nullable=False, default=dict)
    quality_rating = Column(Numeric(12, 6), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    # Lifecycle (MKT-002)
    status = Column(String(40), nullable=False, default="GHOST")  # VendorStatus
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    profile_completeness = Column(Integer, nullable=False, default=0)  # 0-100
    identity_json = Column(JSONB, nullable=False, default=dict)  # tax_id, duns, etc.
    commercial_terms_json = Column(JSONB, nullable=False, default=dict)
    lead_time_profile_json = Column(JSONB, nullable=False, default=dict)
    onboarding_method = Column(String(40), nullable=True)  # platform_seeded, self_onboarded, buyer_invited
    claimed_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    suspended_reason = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    capabilities = relationship(
        "VendorCapability", back_populates="vendor", cascade="all, delete-orphan"
    )
    matches = relationship(
        "VendorMatch", back_populates="vendor", cascade="all, delete-orphan"
    )
    performance_snapshots = relationship(
        "VendorPerformanceSnapshot", back_populates="vendor", cascade="all, delete-orphan"
    )
    import_rows_matched = relationship(
        "VendorImportRow", foreign_keys="VendorImportRow.matched_vendor_id"
    )
    import_rows_created = relationship(
        "VendorImportRow", foreign_keys="VendorImportRow.created_vendor_id"
    )
    identity_aliases = relationship(
        "VendorIdentityAlias", cascade="all, delete-orphan"
    )
    evidence_attachments = relationship(
        "VendorEvidenceAttachment", cascade="all, delete-orphan"
    )


class VendorImportBatch(Base):
    __tablename__ = "vendor_import_batches"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_type", "source_ref", name="uq_vendor_import_batch_source_ref"),
        Index("ix_vendor_import_batch_org", "organization_id"),
        Index("ix_vendor_import_batch_status", "status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    source_type = Column(String(40), nullable=False, default="buyer_approved_list")
    source_ref = Column(String(160), nullable=True)
    file_name = Column(Text, nullable=True)
    file_hash = Column(String(128), nullable=True)
    status = Column(String(40), nullable=False, default="staged")
    import_mode = Column(String(40), nullable=False, default="upsert_safe")
    total_rows = Column(Integer, nullable=False, default=0)
    processed_rows = Column(Integer, nullable=False, default=0)
    success_rows = Column(Integer, nullable=False, default=0)
    failed_rows = Column(Integer, nullable=False, default=0)
    warning_rows = Column(Integer, nullable=False, default=0)
    duplicate_collision_rows = Column(Integer, nullable=False, default=0)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    rows = relationship(
        "VendorImportRow", back_populates="batch", cascade="all, delete-orphan"
    )


class VendorImportRow(Base):
    __tablename__ = "vendor_import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_index", name="uq_vendor_import_row_index"),
        UniqueConstraint("batch_id", "idempotency_key", name="uq_vendor_import_row_idempotency"),
        Index("ix_vendor_import_row_batch", "batch_id"),
        Index("ix_vendor_import_row_status", "status"),
        Index("ix_vendor_import_row_resolution", "resolution_status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_index = Column(Integer, nullable=False)
    status = Column(String(40), nullable=False, default="staged")
    resolution_status = Column(String(40), nullable=False, default="unresolved")
    idempotency_key = Column(String(128), nullable=False)
    raw_row = Column(JSONB, nullable=False, default=dict)
    normalized_identity = Column(JSONB, nullable=False, default=dict)
    parse_errors = Column(JSONB, nullable=False, default=list)
    validation_errors = Column(JSONB, nullable=False, default=list)
    warnings = Column(JSONB, nullable=False, default=list)
    source_confidence_tier = Column(String(40), nullable=True)
    matched_vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolution_confidence = Column(Numeric(12, 6), nullable=True)
    collision_group_key = Column(String(160), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    batch = relationship("VendorImportBatch", back_populates="rows")
    matched_vendor = relationship("Vendor", foreign_keys=[matched_vendor_id])
    created_vendor = relationship("Vendor", foreign_keys=[created_vendor_id])


class VendorIdentityAlias(Base):
    __tablename__ = "vendor_identity_aliases"
    __table_args__ = (
        UniqueConstraint("alias_type", "normalized_value", "vendor_id", name="uq_vendor_identity_alias_vendor_value"),
        Index("ix_vendor_identity_alias_vendor", "vendor_id"),
        Index("ix_vendor_identity_alias_lookup", "alias_type", "normalized_value"),
        Index("ix_vendor_identity_alias_batch_row", "source_batch_id", "source_row_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_type = Column(String(40), nullable=False)
    alias_value = Column(Text, nullable=False)
    normalized_value = Column(Text, nullable=False)
    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    provenance = Column(String(80), nullable=False, default="unknown")
    source_ref = Column(String(160), nullable=True)
    source_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_import_rows.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class VendorEvidenceAttachment(Base):
    __tablename__ = "vendor_evidence_attachments"
    __table_args__ = (
        Index("ix_vendor_evidence_vendor", "vendor_id"),
        Index("ix_vendor_evidence_type", "evidence_type"),
        Index("ix_vendor_evidence_batch_row", "source_batch_id", "source_row_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_type = Column(String(40), nullable=False, default="source_confidence")
    capability_key = Column(String(120), nullable=True)
    certification_name = Column(String(160), nullable=True)
    source_confidence = Column(Numeric(12, 6), nullable=True)
    source_type = Column(String(80), nullable=True)
    source_ref = Column(String(160), nullable=True)
    source_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_import_rows.id", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class VendorCapability(Base):
    __tablename__ = "vendor_capabilities"
    __table_args__ = (
        Index("ix_vendor_cap_vendor", "vendor_id"),
        Index("ix_vendor_cap_process", "process"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    process = Column(Text, nullable=False)
    material_family = Column(Text, nullable=True)
    proficiency = Column(Numeric(6, 4), nullable=False, default=0.8)
    typical_lead_days = Column(Numeric(12, 2), nullable=True)
    certifications = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    vendor = relationship("Vendor", back_populates="capabilities")


class VendorMatchRun(Base):
    __tablename__ = "vendor_match_runs"
    __table_args__ = (
        Index("ix_vmr_project", "project_id"),
        Index("ix_vmr_org", "organization_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    weight_profile = Column(String(40), nullable=True)
    filters_json = Column(JSONB, nullable=False, default=dict)
    weights_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    total_vendors_considered = Column(Integer, nullable=False, default=0)
    total_matches = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    matches = relationship("VendorMatch", back_populates="run", cascade="all, delete-orphan")


class VendorMatch(Base):
    __tablename__ = "vendor_matches"
    __table_args__ = (
        Index("ix_vm_run", "match_run_id"),
        Index("ix_vm_vendor", "vendor_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    match_run_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendor_match_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    rank = Column(Integer, nullable=False, default=0)
    score = Column(Numeric(12, 6), nullable=False, default=0)
    score_breakdown = Column(JSONB, nullable=False, default=dict)
    explanation = Column(Text, nullable=True)
    explanation_json = Column(JSONB, nullable=False, default=dict)
    shortlist_status = Column(Text, nullable=False, default="shortlisted")
    is_primary = Column(Boolean, nullable=False, default=False)
    elimination_reasons = Column(JSONB, nullable=False, default=list)  # PC-005
    confidence_level = Column(String(20), nullable=True)  # HIGH, MEDIUM, LOW
    evidence_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)

    run = relationship("VendorMatchRun", back_populates="matches")
    vendor = relationship("Vendor", back_populates="matches")


class VendorPerformanceSnapshot(Base):
    """Nightly-rebuilt 90-day performance snapshot per vendor."""
    __tablename__ = "vendor_performance_snapshots"
    __table_args__ = (
        Index("ix_vps_vendor_date", "vendor_id", "snapshot_date"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_date = Column(Date, nullable=False)
    total_pos = Column(Integer, nullable=False, default=0)
    on_time_delivery_pct = Column(Numeric(8, 4), nullable=True)
    quality_pass_pct = Column(Numeric(8, 4), nullable=True)
    avg_response_time_hours = Column(Numeric(12, 4), nullable=True)
    quote_win_rate = Column(Numeric(8, 4), nullable=True)
    trailing_window_days = Column(Integer, nullable=False, default=90)
    computed_at = Column(DateTime(timezone=True), default=_now)

    vendor = relationship("Vendor", back_populates="performance_snapshots")