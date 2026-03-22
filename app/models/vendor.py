"""Vendor model."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Boolean, DateTime, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    country = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True)
    capabilities = Column(JSON, nullable=True)
    rating = Column(Float, default=3.0)
    reliability_score = Column(Float, default=0.5)
    avg_lead_time = Column(Float, default=14.0)
    contact_email = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pricing_history = relationship("PricingHistory", back_populates="vendor", cascade="all, delete-orphan")
    memory = relationship("SupplierMemory", back_populates="vendor", uselist=False, cascade="all, delete-orphan")
