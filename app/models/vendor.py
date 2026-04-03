"""Vendor model — maps to pricing.vendors and pricing.vendor_capabilities in PostgreSQL."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Boolean, DateTime, Numeric, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = {"schema": "pricing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(Text, nullable=False)
    legal_name = Column(Text, nullable=True)
    country_id = Column(UUID(as_uuid=False), nullable=True)
    region_id = Column(UUID(as_uuid=False), nullable=True)
    website = Column(Text, nullable=True)
    contact_email = Column(Text, nullable=True)
    contact_phone = Column(Text, nullable=True)
    reliability_score = Column(Numeric(12, 6), nullable=False, default=1.0)
    avg_lead_time_days = Column(Numeric(12, 2), nullable=True)
    default_currency = Column(String(3), nullable=False, default="USD")
    default_moq = Column(Numeric(18, 6), nullable=True)
    lead_time_profile = Column(JSONB, nullable=False, default=dict)
    incoterms = Column(JSONB, nullable=False, default=list)
    payment_terms = Column(JSONB, nullable=False, default=list)
    regions_served = Column(JSONB, nullable=False, default=list)
    certifications = Column(JSONB, nullable=False, default=list)
    capacity_profile = Column(JSONB, nullable=False, default=dict)
    quality_rating = Column(Numeric(12, 6), nullable=False, default=0)
    logistics_capability = Column(JSONB, nullable=False, default=dict)
    sample_order_available = Column(Boolean, nullable=False, default=False)
    quote_validity_days = Column(Integer, nullable=False, default=14)
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def country(self):
        return str(self.metadata_.get("country_name", "")) if self.metadata_ else ""

    @country.setter
    def country(self, value):
        if not self.metadata_:
            self.metadata_ = {}
        self.metadata_["country_name"] = value

    @property
    def region(self):
        return str(self.metadata_.get("region_name", "")) if self.metadata_ else ""

    @region.setter
    def region(self, value):
        if not self.metadata_:
            self.metadata_ = {}
        self.metadata_["region_name"] = value

    @property
    def capabilities(self):
        return (self.metadata_ or {}).get("capabilities", [])

    @capabilities.setter
    def capabilities(self, value):
        if not self.metadata_:
            self.metadata_ = {}
        self.metadata_["capabilities"] = value or []

    @property
    def rating(self):
        return float(self.reliability_score * 5) if self.reliability_score else 3.0

    @rating.setter
    def rating(self, value):
        self.reliability_score = (value or 3.0) / 5.0

    @property
    def avg_lead_time(self):
        return float(self.avg_lead_time_days) if self.avg_lead_time_days else 14.0

    @avg_lead_time.setter
    def avg_lead_time(self, value):
        self.avg_lead_time_days = value

    @property
    def moq(self):
        return float(self.default_moq) if self.default_moq is not None else None

    @moq.setter
    def moq(self, value):
        self.default_moq = value

    @property
    def currency(self):
        return self.default_currency

    @currency.setter
    def currency(self, value):
        self.default_currency = value or "USD"

    pricing_history = relationship("PricingQuote", back_populates="vendor", cascade="all, delete-orphan")
    memory = relationship("SupplierMemory", back_populates="vendor", uselist=False, cascade="all, delete-orphan")
    capability_entries = relationship("VendorCapability", back_populates="vendor", cascade="all, delete-orphan")
    match_records = relationship("VendorMatch", back_populates="vendor", cascade="all, delete-orphan")

class VendorCapability(Base):
    """Per-vendor capability record — queryable for matching.
    Replaces the JSONB capabilities list in vendor.metadata_ with
    a proper table for indexed queries like:
      SELECT v.* FROM vendors v JOIN vendor_capabilities vc
      ON v.id = vc.vendor_id WHERE vc.process = 'CNC' AND vc.proficiency >= 0.8
    """
    __tablename__ = "vendor_capabilities"
    __table_args__ = (
        Index("ix_vendor_cap_process", "process"),
        Index("ix_vendor_cap_material", "material_family"),
        Index("ix_vendor_cap_vendor", "vendor_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    process = Column(Text, nullable=False)  # CNC, sheet_metal, injection_molding, etc.
    material_family = Column(Text, nullable=True)  # stainless_steel, aluminum, plastic, etc.
    proficiency = Column(Numeric(6, 4), nullable=False, default=0.8)  # 0-1 score
    min_quantity = Column(Numeric(18, 6), nullable=True)
    max_quantity = Column(Numeric(18, 6), nullable=True)
    typical_lead_days = Column(Numeric(12, 2), nullable=True)
    certifications = Column(JSONB, nullable=False, default=list)  # ["ISO9001", "AS9100"]
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="capability_entries")
