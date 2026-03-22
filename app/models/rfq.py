"""RFQ and RFQ item models."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class RFQStatus(str, enum.Enum):
    created = "created"
    sent = "sent"
    quoted = "quoted"
    approved = "approved"
    rejected = "rejected"
    in_production = "in_production"
    completed = "completed"


class RFQ(Base):
    __tablename__ = "rfqs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    bom_id = Column(String(36), ForeignKey("boms.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), default=RFQStatus.created.value)
    selected_vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    total_estimated_cost = Column(Float, nullable=True)
    total_final_cost = Column(Float, nullable=True)
    currency = Column(String(10), default="USD")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")
    tracking = relationship("ProductionTracking", back_populates="rfq", cascade="all, delete-orphan")
    feedback = relationship("ExecutionFeedback", back_populates="rfq", uselist=False, cascade="all, delete-orphan")


class RFQItem(Base):
    __tablename__ = "rfq_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    part_name = Column(String(500), nullable=True)
    quantity = Column(Integer, default=1)
    material = Column(String(255), nullable=True)
    process = Column(String(100), nullable=True)
    quoted_price = Column(Float, nullable=True)
    final_price = Column(Float, nullable=True)
    lead_time = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="items")
