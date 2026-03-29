"""Strategy run model — maps to projects.strategy_runs.

Each strategy computation is persisted as a versioned, immutable run.
project.current_strategy_run_id points to the latest active run.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (
        Index("ix_strategy_runs_project", "project_id"),
        Index("ix_strategy_runs_version", "project_id", "version"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    analysis_id = Column(UUID(as_uuid=False), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    priority = Column(Text, nullable=False, default="cost")  # cost or speed
    delivery_location = Column(Text, nullable=True)
    target_currency = Column(Text, nullable=False, default="USD")
    strategy_json = Column(JSONB, nullable=False, default=dict)
    procurement_json = Column(JSONB, nullable=False, default=dict)
    global_optimization = Column(JSONB, nullable=False, default=dict)
    region_distribution = Column(JSONB, nullable=False, default=dict)
    part_level_decisions = Column(JSONB, nullable=False, default=list)
    recommended_location = Column(Text, nullable=True)
    average_cost = Column(Numeric(18, 6), nullable=True)
    savings_percent = Column(Numeric(12, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    decision_summary = Column(Text, nullable=True)
    total_parts = Column(Integer, nullable=False, default=0)
    engine_version = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
