"""Pricing history model."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class PricingHistory(Base):
    __tablename__ = "pricing_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    part_name = Column(String(500), nullable=True)
    material = Column(String(255), nullable=True)
    process = Column(String(100), nullable=True)
    quantity = Column(Integer, default=1)
    price = Column(Float, nullable=False)
    currency = Column(String(10), default="USD")
    region = Column(String(50), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="pricing_history")
