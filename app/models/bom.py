import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Numeric, Boolean, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class BOM(Base):
    __tablename__ = "boms"
    __table_args__ = (
        Index("ix_boms_user_id", "uploaded_by_user_id"),
        Index("ix_boms_guest_session", "guest_session_id"),
        Index("ix_boms_project_id", "project_id"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    uploaded_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    source_file_name = Column(Text, nullable=False, default="upload.csv")
    source_file_type = Column(Text, nullable=False, default="csv")
    source_checksum = Column(Text, nullable=True)
    original_filename = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    target_currency = Column(String(3), nullable=False, default="USD")
    delivery_location = Column(Text, nullable=True)
    priority = Column(Text, nullable=False, default="balanced")
    status = Column(Text, nullable=False, default="uploaded")
    total_parts = Column(Integer, nullable=False, default=0)
    raw_payload = Column(JSONB, nullable=False, default=dict)
    parse_summary = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship("User", back_populates="boms", foreign_keys=[uploaded_by_user_id])
    parts = relationship("BOMPart", back_populates="bom", cascade="all, delete-orphan")
    analysis = relationship("AnalysisResult", back_populates="bom", uselist=False, cascade="all, delete-orphan")
    rfqs = relationship("RFQBatch", back_populates="bom")


class BOMPart(Base):
    __tablename__ = "bom_parts"
    __table_args__ = (
        Index("ix_bom_parts_bom_id", "bom_id"),
        Index("ix_bom_parts_canonical_key", "canonical_part_key"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Text, nullable=False, default="")
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
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
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom = relationship("BOM", back_populates="parts")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_analysis_bom", "bom_id"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    report_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    strategy_json = Column(JSONB, nullable=False, default=dict)
    scoring_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom = relationship("BOM", back_populates="analysis")
