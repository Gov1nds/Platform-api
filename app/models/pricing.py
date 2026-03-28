"""Pricing model — maps to pricing.pricing_quotes in PostgreSQL.

The old 'PricingHistory' model is replaced by 'PricingQuote' which maps
to the production schema. A PricingHistory alias is kept for backward compat.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, String, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class PricingQuote(Base):
    __tablename__ = "pricing_quotes"
    __table_args__ = (
        Index("ix_pricing_key_fresh", "canonical_part_key", "freshness_state"),
        Index("ix_pricing_recorded", "recorded_at"),
        Index("ix_pricing_vendor", "vendor_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    part_master_id = Column(UUID(as_uuid=False), nullable=True)
    canonical_part_key = Column(Text, nullable=False, default="")
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    region_id = Column(UUID(as_uuid=False), nullable=True)
    source_id = Column(UUID(as_uuid=False), nullable=True)
    source_snapshot_id = Column(UUID(as_uuid=False), nullable=True)
    source_type = Column(Text, nullable=False, default="fallback_estimate")
    source_currency = Column(String(3), nullable=False, default="USD")
    display_currency = Column(String(3), nullable=False, default="USD")
    fx_rate = Column(Numeric(18, 8), nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
    moq = Column(Numeric(18, 6), nullable=True)
    unit_price = Column(Numeric(18, 6), nullable=False, default=0)
    total_price = Column(Numeric(18, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    confidence = Column(Numeric(12, 6), nullable=False, default=1.0)
    freshness_state = Column(Text, nullable=False, default="current")
    valid_from = Column(DateTime(timezone=True), default=datetime.utcnow)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    recorded_by = Column(UUID(as_uuid=False), nullable=True)
    quote_payload = Column(JSONB, nullable=False, default=dict)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases for code that uses old PricingHistory fields
    @property
    def normalized_key(self):
        return self.canonical_part_key

    @normalized_key.setter
    def normalized_key(self, value):
        self.canonical_part_key = value or ""

    @property
    def mpn(self):
        return (self.quote_payload or {}).get("mpn", "")

    @mpn.setter
    def mpn(self, value):
        if not self.quote_payload:
            self.quote_payload = {}
        self.quote_payload["mpn"] = value or ""

    @property
    def part_name(self):
        return self.canonical_part_key

    @part_name.setter
    def part_name(self, value):
        self.canonical_part_key = value or ""

    @property
    def price(self):
        return float(self.unit_price) if self.unit_price is not None else 0.0

    @price.setter
    def price(self, value):
        self.unit_price = value or 0

    @property
    def material(self):
        return (self.quote_payload or {}).get("material", "")

    @material.setter
    def material(self, value):
        if not self.quote_payload:
            self.quote_payload = {}
        self.quote_payload["material"] = value or ""

    @property
    def process(self):
        return (self.quote_payload or {}).get("process", "")

    @property
    def currency(self):
        return self.display_currency

    @currency.setter
    def currency(self, value):
        self.display_currency = value or "USD"

    @property
    def region(self):
        return (self.quote_payload or {}).get("region", "")

    @region.setter
    def region(self, value):
        if not self.quote_payload:
            self.quote_payload = {}
        self.quote_payload["region"] = value or ""

    @property
    def is_current(self):
        return self.freshness_state == "current"

    @is_current.setter
    def is_current(self, value):
        self.freshness_state = "current" if value else "stale"

    @property
    def is_simulated(self):
        return (self.quote_payload or {}).get("is_simulated", False)

    @is_simulated.setter
    def is_simulated(self, value):
        if not self.quote_payload:
            self.quote_payload = {}
        self.quote_payload["is_simulated"] = bool(value)

    vendor = relationship("Vendor", back_populates="pricing_history")


# Alias for backward compatibility with existing code
PricingHistory = PricingQuote
