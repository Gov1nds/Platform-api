import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Numeric, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = {"schema": "pricing"}

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
    default_moq = Column(Numeric(18, 6), nullable=True)
    regions_served = Column(JSONB, nullable=False, default=list)
    certifications = Column(JSONB, nullable=False, default=list)
    capacity_profile = Column(JSONB, nullable=False, default=dict)
    quality_rating = Column(Numeric(12, 6), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    capabilities = relationship("VendorCapability", back_populates="vendor", cascade="all, delete-orphan")
    matches = relationship("VendorMatch", back_populates="vendor", cascade="all, delete-orphan")


class VendorCapability(Base):
    __tablename__ = "vendor_capabilities"
    __table_args__ = (
        Index("ix_vendor_cap_vendor", "vendor_id"),
        Index("ix_vendor_cap_process", "process"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
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
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    filters_json = Column(JSONB, nullable=False, default=dict)
    weights_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    total_vendors_considered = Column(Integer, nullable=False, default=0)
    total_matches = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)

    matches = relationship("VendorMatch", back_populates="run", cascade="all, delete-orphan")


class VendorMatch(Base):
    __tablename__ = "vendor_matches"
    __table_args__ = (
        Index("ix_vm_run", "match_run_id"),
        Index("ix_vm_vendor", "vendor_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    match_run_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendor_match_runs.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    rank = Column(Integer, nullable=False, default=0)
    score = Column(Numeric(12, 6), nullable=False, default=0)
    score_breakdown = Column(JSONB, nullable=False, default=dict)
    explanation = Column(Text, nullable=True)
    explanation_json = Column(JSONB, nullable=False, default=dict)
    shortlist_status = Column(Text, nullable=False, default="shortlisted")
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    run = relationship("VendorMatchRun", back_populates="matches")
    vendor = relationship("Vendor", back_populates="matches")
