"""Analysis result model — stores FULL analyzer + strategy output."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(String(36), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    raw_analyzer_output = Column(JSON, nullable=True)
    strategy_output = Column(JSON, nullable=True)
    enriched_output = Column(JSON, nullable=True)
    recommended_location = Column(String(100), nullable=True)
    average_cost = Column(Float, nullable=True)
    cost_range_low = Column(Float, nullable=True)
    cost_range_high = Column(Float, nullable=True)
    savings_percent = Column(Float, nullable=True)
    lead_time = Column(Float, nullable=True)
    decision_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bom = relationship("BOM", back_populates="analysis")
    cost_savings = relationship("CostSavings", back_populates="analysis", uselist=False, cascade="all, delete-orphan")


class CostSavings(Base):
    __tablename__ = "cost_savings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String(36), ForeignKey("analysis_results.id", ondelete="CASCADE"), nullable=False, unique=True)
    recommended_cost = Column(Float, nullable=True)
    alternative_cost = Column(Float, nullable=True)
    savings_percent = Column(Float, nullable=True)
    savings_value = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analysis = relationship("AnalysisResult", back_populates="cost_savings")
