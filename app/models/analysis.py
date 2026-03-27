"""Analysis result model — maps to bom.analysis_results in PostgreSQL."""
import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = {"schema": "bom"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False, unique=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    raw_analyzer_output = Column(JSONB, nullable=False, default=dict)
    structured_output = Column(JSONB, nullable=False, default=dict)
    quality_score = Column(Numeric(12, 6), nullable=True)
    decision_summary = Column(Text, nullable=True)
    recommended_location = Column(Text, nullable=True)
    average_cost = Column(Numeric(18, 6), nullable=True)
    cost_range_low = Column(Numeric(18, 6), nullable=True)
    cost_range_high = Column(Numeric(18, 6), nullable=True)
    savings_percent = Column(Numeric(12, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    source_version = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def lead_time(self):
        return float(self.lead_time_days) if self.lead_time_days is not None else None

    @lead_time.setter
    def lead_time(self, value):
        self.lead_time_days = value

    @property
    def strategy_output(self):
        return (self.structured_output or {}).get("strategy", {})

    @strategy_output.setter
    def strategy_output(self, value):
        if not self.structured_output:
            self.structured_output = {}
        self.structured_output["strategy"] = value

    @property
    def enriched_output(self):
        return (self.structured_output or {}).get("enriched", {})

    @enriched_output.setter
    def enriched_output(self, value):
        if not self.structured_output:
            self.structured_output = {}
        self.structured_output["enriched"] = value

    bom = relationship("BOM", back_populates="analysis")
