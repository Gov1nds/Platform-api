"""RFQ, RFQ item, Drawing asset, and Quote models."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, Text, Boolean
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
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)  # NEW
    status = Column(String(30), default=RFQStatus.created.value)
    selected_vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    total_estimated_cost = Column(Float, nullable=True)
    total_final_cost = Column(Float, nullable=True)
    currency = Column(String(10), default="USD")  # Will be set from project, not hardcoded
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")
    tracking = relationship("ProductionTracking", back_populates="rfq", cascade="all, delete-orphan")
    feedback = relationship("ExecutionFeedback", back_populates="rfq", uselist=False, cascade="all, delete-orphan")
    quotes = relationship("RFQQuote", back_populates="rfq", cascade="all, delete-orphan")  # NEW


class RFQItem(Base):
    __tablename__ = "rfq_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    bom_part_id = Column(String(36), ForeignKey("bom_parts.id", ondelete="SET NULL"), nullable=True, index=True)  # NEW FK
    part_name = Column(String(500), nullable=True)
    quantity = Column(Integer, default=1)
    material = Column(String(255), nullable=True)
    process = Column(String(100), nullable=True)
    quoted_price = Column(Float, nullable=True)
    final_price = Column(Float, nullable=True)
    lead_time = Column(Float, nullable=True)
    drawing_required = Column(Boolean, default=False)  # NEW
    drawing_asset_id = Column(String(36), nullable=True)  # NEW
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="items")


class DrawingAsset(Base):
    """NEW: Stores metadata for uploaded drawings."""
    __tablename__ = "drawing_assets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    bom_id = Column(String(36), ForeignKey("boms.id", ondelete="SET NULL"), nullable=True)
    bom_part_id = Column(String(36), ForeignKey("bom_parts.id", ondelete="SET NULL"), nullable=True)
    storage_provider = Column(String(50), default="local")  # local/s3/gcs/r2
    storage_path = Column(String(1000), nullable=True)
    file_name = Column(String(500), nullable=True)
    file_hash = Column(String(128), nullable=True)
    mime_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)
    version = Column(Integer, default=1)
    uploaded_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RFQQuote(Base):
    """NEW: Stores vendor quotes received for an RFQ."""
    __tablename__ = "rfq_quotes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    vendor_id = Column(String(36), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), default="pending")
    quote_currency = Column(String(10), default="USD")
    quote_valid_until = Column(DateTime, nullable=True)
    quote_received_at = Column(DateTime, nullable=True)
    total_quote_value = Column(Float, nullable=True)
    confidence = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="quotes")