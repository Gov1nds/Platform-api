"""
BOM upload, BOM line, and analysis result models.

References: GAP-011, GAP-004 (SM-001), GAP-029, GAP-016, GAP-025,
            architecture.md CC-04/CC-14, canonical-domain-model.md BC-04
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Integer, DateTime, ForeignKey, Numeric,
    Boolean, BigInteger, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class BOM(Base):
    """
    BOM Upload entity (ENT-010).

    Represents a single file upload. Status follows BOMUploadStatus enum:
    PENDING → PARSING → AWAITING_MAPPING_CONFIRM → MAPPING_CONFIRMED → INGESTED | PARSE_FAILED
    """
    __tablename__ = "boms"
    __table_args__ = (
        Index("ix_boms_user_id", "uploaded_by_user_id"),
        Index("ix_boms_guest_session", "guest_session_id"),
        Index("ix_boms_project_id", "project_id"),
        Index("ix_boms_org", "organization_id"),
        Index("ix_boms_file_hash", "file_hash"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    uploaded_by_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    guest_session_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # File metadata
    source_file_name = Column(Text, nullable=False, default="upload.csv")
    source_file_type = Column(Text, nullable=False, default="csv")
    source_checksum = Column(Text, nullable=True)
    original_filename = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    file_hash = Column(String(128), nullable=True)  # SHA-256 dedup (GAP-011)
    s3_key = Column(Text, nullable=True)  # replaces local disk (INT-007)

    # Context
    target_currency = Column(String(3), nullable=False, default="USD")
    delivery_location = Column(Text, nullable=True)
    priority = Column(Text, nullable=False, default="balanced")

    # Status & parsing
    status = Column(String(40), nullable=False, default="PENDING")  # BOMUploadStatus
    scan_status = Column(String(40), nullable=True)  # PENDING, CLEAN, INFECTED, ERROR
    column_mapping_json = Column(JSONB, nullable=False, default=dict)
    total_parts = Column(Integer, nullable=False, default=0)
    parse_summary = Column(JSONB, nullable=False, default=dict)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="boms", foreign_keys=[uploaded_by_user_id])
    parts = relationship("BOMPart", back_populates="bom", cascade="all, delete-orphan")
    analysis = relationship(
        "AnalysisResult", back_populates="bom", uselist=False, cascade="all, delete-orphan"
    )
    rfqs = relationship("RFQBatch", back_populates="bom")


class BOMPart(Base):
    """
    BOM Line entity (ENT-011).

    Status follows BOMLineStatus enum (SM-001, 17 states):
    RAW → NORMALIZING → NORMALIZED → ENRICHING → ENRICHED → SCORING → SCORED → …
    raw_text is immutable after creation.
    """
    __tablename__ = "bom_parts"
    __table_args__ = (
        Index("ix_bom_parts_bom_id", "bom_id"),
        Index("ix_bom_parts_canonical_key", "canonical_part_key"),
        Index("ix_bom_parts_status", "bom_id", "status"),
        Index("ix_bom_parts_org", "organization_id"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(UUID(as_uuid=False), nullable=True)

    # SM-001 lifecycle
    status = Column(String(40), nullable=False, default="RAW")  # BOMLineStatus
    row_number = Column(Integer, nullable=True)
    source_type = Column(String(40), nullable=False, default="file")  # file, text, api

    # Original fields (raw_text is immutable)
    item_id = Column(Text, nullable=False, default="")
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(20, 8), nullable=False, default=1)
    unit = Column(Text, nullable=True)
    part_number = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    supplier_name = Column(Text, nullable=True)
    category_code = Column(Text, nullable=True)
    procurement_class = Column(Text, nullable=False, default="unknown")
    material = Column(Text, nullable=True)
    material_form = Column(Text, nullable=True)
    geometry = Column(Text, nullable=True)
    tolerance = Column(Text, nullable=True)
    secondary_ops = Column(JSONB, nullable=False, default=list)
    specs = Column(JSONB, nullable=False, default=dict)
    classification_confidence = Column(Numeric(12, 6), nullable=False, default=0)
    classification_reason = Column(Text, nullable=True)
    has_mpn = Column(Boolean, nullable=False, default=False)
    is_custom = Column(Boolean, nullable=False, default=False)
    is_raw = Column(Boolean, nullable=False, default=False)
    rfq_required = Column(Boolean, nullable=False, default=False)
    drawing_required = Column(Boolean, nullable=False, default=False)
    canonical_part_key = Column(Text, nullable=True)
    review_status = Column(Text, nullable=True, default="auto")
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    # Intelligence pipeline output persistence (CC-14: platform-api persists)
    normalization_status = Column(String(40), nullable=True)
    normalization_trace_json = Column(JSONB, nullable=False, default=dict)
    enrichment_status = Column(String(40), nullable=True)
    enrichment_json = Column(JSONB, nullable=False, default=dict)
    scoring_status = Column(String(40), nullable=True)
    score_cache_json = Column(JSONB, nullable=False, default=dict)
    strategy_json = Column(JSONB, nullable=False, default=dict)
    risk_flags = Column(JSONB, nullable=False, default=list)
    data_freshness_json = Column(JSONB, nullable=False, default=dict)
    review_required = Column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    bom = relationship("BOM", back_populates="parts")


class AnalysisResult(Base):
    """Snapshot of a full-BOM analysis run."""
    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_analysis_bom", "bom_id"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    algorithm_version = Column(String(40), nullable=True)
    report_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    strategy_json = Column(JSONB, nullable=False, default=dict)
    scoring_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    bom = relationship("BOM", back_populates="analysis")