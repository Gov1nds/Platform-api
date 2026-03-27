"""Vendor model — maps to pricing.vendors in PostgreSQL."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Boolean, DateTime, Numeric
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

    pricing_history = relationship("PricingQuote", back_populates="vendor", cascade="all, delete-orphan")
    memory = relationship("SupplierMemory", back_populates="vendor", uselist=False, cascade="all, delete-orphan")
