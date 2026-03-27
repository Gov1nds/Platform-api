"""Production tracking and execution feedback models.

These tables are NOT in the bootstrap PostgreSQL schema. They are created
by the supplementary migration (002_add_tracking_tables.py).
We keep them in the 'ops' schema to align with the bootstrap conventions.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class TrackingStage(str, enum.Enum):
    T0 = "T0"  # Order placed
    T1 = "T1"  # Material procurement
    T2 = "T2"  # Manufacturing started
    T3 = "T3"  # QC / inspection
    T4 = "T4"  # Shipped / delivered


class ProductionTracking(Base):
    __tablename__ = "production_tracking"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(Text, default=TrackingStage.T0.value)
    status_message = Column(Text, nullable=True)
    progress_percent = Column(Integer, default=0)
    updated_by = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", foreign_keys=[rfq_id])


class ExecutionFeedback(Base):
    __tablename__ = "execution_feedback"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False, unique=True)
    predicted_cost = Column(Numeric(18, 6), nullable=True)
    actual_cost = Column(Numeric(18, 6), nullable=True)
    cost_delta = Column(Numeric(18, 6), nullable=True)
    predicted_lead_time = Column(Numeric(12, 2), nullable=True)
    actual_lead_time = Column(Numeric(12, 2), nullable=True)
    lead_time_delta = Column(Numeric(12, 2), nullable=True)
    feedback_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", foreign_keys=[rfq_id])
