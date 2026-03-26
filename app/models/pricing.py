"""Pricing history model — FIXED: append-only with freshness metadata."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class PricingHistory(Base):
    __tablename__ = "pricing_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    part_name = Column(String(500), nullable=True)
    normalized_key = Column(String(500), nullable=True, index=True)  # NEW
    mpn = Column(String(255), nullable=True, index=True)             # NEW
    material = Column(String(255), nullable=True)
    process = Column(String(100), nullable=True)
    quantity = Column(Integer, default=1)
    price = Column(Float, nullable=False)
    # FIXED: currency handling
    source_currency = Column(String(10), default="USD")              # NEW
    display_currency = Column(String(10), default="USD")             # NEW
    currency = Column(String(10), default="USD")
    region = Column(String(50), nullable=True)
    # FIXED: freshness metadata
    confidence = Column(String(20), default="low")                   # NEW: high/medium/low
    freshness_state = Column(String(20), default="current")          # NEW: current/stale/expired
    valid_until = Column(DateTime, nullable=True)                    # NEW
    is_current = Column(Boolean, default=True)                       # NEW
    is_simulated = Column(Boolean, default=False)                    # NEW: marks non-real data
    source_type = Column(String(50), nullable=True)                  # NEW: external_api/rfq_actual/fallback_estimate/internal_db
    recorded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="pricing_history")