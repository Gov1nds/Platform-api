"""
Part-to-Vendor matching models (Phase 3).

References: Execution Plan §2 (Part → Vendor Matching), migration 013.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class PartVendorIndex(Base):
    """
    Per canonical-part × vendor evidence and match classification row.

    Award-ready gating per Execution Plan §2: true ONLY if category fit is
    strong, lead-time data exists, fresh quote history exists, and
    vendor reliability is good. Otherwise rfq_first_recommended=True.
    """
    __tablename__ = "part_vendor_index"
    __table_args__ = (
        UniqueConstraint("canonical_part_key", "vendor_id", name="uq_part_vendor_index_part_vendor"),
        Index("ix_part_vendor_index_part", "canonical_part_key"),
        Index("ix_part_vendor_index_vendor", "vendor_id"),
        Index("ix_part_vendor_index_award", "award_ready"),
        Index("ix_part_vendor_index_score", "match_score"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    canonical_part_key = Column(Text, nullable=False)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_type = Column(String(40), nullable=False, default="partial_category")
    match_score = Column(Numeric(6, 4), nullable=False, default=0)
    evidence_count = Column(Integer, nullable=False, default=0)
    last_quote_price = Column(Numeric(20, 8), nullable=True)
    last_quote_currency = Column(String(3), nullable=True)
    last_quote_date = Column(Date, nullable=True)
    last_po_date = Column(Date, nullable=True)
    po_win_count = Column(Integer, nullable=False, default=0)
    rfq_sent_count = Column(Integer, nullable=False, default=0)
    award_ready = Column(Boolean, nullable=False, default=False)
    rfq_first_recommended = Column(Boolean, nullable=False, default=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0)
    category_match_detail = Column(JSONB, nullable=False, default=dict)
    material_match_detail = Column(JSONB, nullable=False, default=dict)
    process_match_detail = Column(JSONB, nullable=False, default=dict)
    alias_match_detail = Column(JSONB, nullable=False, default=dict)
    historical_evidence = Column(JSONB, nullable=False, default=list)
    last_updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    created_at = Column(DateTime(timezone=True), default=_now)

    vendor = relationship("Vendor")
