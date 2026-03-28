"""Supplier memory model — maps to pricing.supplier_memory + pricing.supplier_memory_history."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class SupplierMemory(Base):
    """Current supplier memory state — one row per vendor, updated in place."""
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

    @property
    def id(self):
        return self.vendor_id

    vendor = relationship("Vendor", back_populates="memory")
    history = relationship("SupplierMemoryHistory", back_populates="memory",
                           cascade="all, delete-orphan", order_by="desc(SupplierMemoryHistory.recorded_at)")


class SupplierMemoryHistory(Base):
    """Append-only history of supplier memory snapshots. Written every time
    SupplierMemory is updated so previous scores are never lost."""
    __tablename__ = "supplier_memory_history"
    __table_args__ = (
        Index("ix_memory_history_vendor", "vendor_id"),
        Index("ix_memory_history_recorded", "recorded_at"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    performance_score = Column(Numeric(12, 6), nullable=False)
    cost_accuracy_score = Column(Numeric(12, 6), nullable=False)
    delivery_accuracy_score = Column(Numeric(12, 6), nullable=False)
    risk_level = Column(Text, nullable=False)
    total_orders = Column(Integer, nullable=False, default=0)
    avg_cost_delta_pct = Column(Numeric(12, 6), nullable=True)
    avg_lead_delta_days = Column(Numeric(12, 2), nullable=True)
    event_type = Column(Text, nullable=False, default="update")  # update, feedback, decay
    event_payload = Column(JSONB, nullable=False, default=dict)
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    memory = relationship("SupplierMemory", back_populates="history",
                           foreign_keys=[vendor_id])
