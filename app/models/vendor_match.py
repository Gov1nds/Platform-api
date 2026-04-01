"""Vendor match persistence — shortlisted ranking + immutable runs."""
import uuid
from datetime import datetime

from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Boolean, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class VendorMatchRun(Base):
    __tablename__ = "vendor_match_runs"
    __table_args__ = (
        Index("ix_vendor_match_runs_project", "project_id"),
        Index("ix_vendor_match_runs_created", "created_at"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    filters_json = Column(JSONB, nullable=False, default=dict)
    constraints_json = Column(JSONB, nullable=False, default=dict)
    strategy_snapshot = Column(JSONB, nullable=False, default=dict)
    analysis_snapshot = Column(JSONB, nullable=False, default=dict)
    weights_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)

    total_vendors_considered = Column(Integer, nullable=False, default=0)
    total_matches = Column(Integer, nullable=False, default=0)
    shortlist_size = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    matches = relationship("VendorMatch", back_populates="run", cascade="all, delete-orphan")


class VendorMatch(Base):
    __tablename__ = "vendor_matches"
    __table_args__ = (
        Index("ix_vendor_matches_run", "match_run_id"),
        Index("ix_vendor_matches_project", "project_id"),
        Index("ix_vendor_matches_vendor", "vendor_id"),
        Index("ix_vendor_matches_rank", "rank"),
        Index("ix_vendor_matches_score", "score"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_run_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendor_match_runs.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)

    rank = Column(Integer, nullable=False, default=0)
    score = Column(Numeric(12, 6), nullable=False, default=0)

    score_breakdown = Column(JSONB, nullable=False, default=dict)
    reason_codes = Column(JSONB, nullable=False, default=list)
    explanation_json = Column(JSONB, nullable=False, default=dict)
    constraint_inputs = Column(JSONB, nullable=False, default=dict)
    scorecard_json = Column(JSONB, nullable=False, default=dict)
    part_rationales = Column(JSONB, nullable=False, default=list)

    shortlist_status = Column(Text, nullable=False, default="shortlisted")
    response_status = Column(Text, nullable=False, default="uncontacted")
    feedback_rating = Column(Numeric(6, 2), nullable=True)
    feedback_notes = Column(Text, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    run = relationship("VendorMatchRun", back_populates="matches")
    vendor = relationship("Vendor", back_populates="match_records")