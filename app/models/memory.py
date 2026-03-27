"""Supplier memory model — maps to pricing.supplier_memory."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class SupplierMemory(Base):
    __tablename__ = "supplier_memory"
    __table_args__ = {"schema": "pricing"}

    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), primary_key=True)
    performance_score = Column(Numeric(12, 6), nullable=False, default=1.0)
    cost_accuracy_score = Column(Numeric(12, 6), nullable=False, default=1.0)
    delivery_accuracy_score = Column(Numeric(12, 6), nullable=False, default=1.0)
    risk_level = Column(Text, nullable=False, default="medium")
    total_orders = Column(Integer, nullable=False, default=0)
    avg_cost_delta_pct = Column(Numeric(12, 6), nullable=True)
    avg_lead_delta_days = Column(Numeric(12, 2), nullable=True)
    last_updated = Column("last_updated", DateTime(timezone=True), default=datetime.utcnow)
    summary = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward compat: old model had a separate id column
    @property
    def id(self):
        return self.vendor_id

    vendor = relationship("Vendor", back_populates="memory")
