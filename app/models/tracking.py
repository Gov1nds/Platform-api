"""Production tracking and execution feedback models."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, Text
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

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(10), default=TrackingStage.T0.value)
    status_message = Column(Text, nullable=True)
    progress_percent = Column(Integer, default=0)
    updated_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="tracking")


class ExecutionFeedback(Base):
    __tablename__ = "execution_feedback"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, unique=True)
    predicted_cost = Column(Float, nullable=True)
    actual_cost = Column(Float, nullable=True)
    cost_delta = Column(Float, nullable=True)
    predicted_lead_time = Column(Float, nullable=True)
    actual_lead_time = Column(Float, nullable=True)
    lead_time_delta = Column(Float, nullable=True)
    feedback_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="feedback")
