"""Supplier memory model — learning from execution feedback."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class SupplierMemory(Base):
    __tablename__ = "supplier_memory"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    performance_score = Column(Float, default=0.5)
    cost_accuracy_score = Column(Float, default=0.5)
    delivery_accuracy_score = Column(Float, default=0.5)
    risk_level = Column(Float, default=0.3)
    total_orders = Column(Float, default=0)
    avg_cost_delta_pct = Column(Float, default=0.0)
    avg_lead_delta_days = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="memory")
