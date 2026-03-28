"""Project model — maps to projects.projects and projects.project_events."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_guest_session", "guest_session_id"),
        Index("ix_projects_status", "status"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    name = Column(Text, nullable=False, default="Uploaded BOM")
    file_name = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="draft")
    visibility = Column(Text, nullable=False, default="private")
    total_parts = Column(Integer, nullable=False, default=0)
    recommended_location = Column(Text, nullable=True)
    average_cost = Column(Numeric(18, 6), nullable=True)
    cost_range_low = Column(Numeric(18, 6), nullable=True)
    cost_range_high = Column(Numeric(18, 6), nullable=True)
    savings_percent = Column(Numeric(12, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    decision_summary = Column(Text, nullable=True)
    current_analysis_id = Column(UUID(as_uuid=False), nullable=True)
    current_strategy_run_id = Column(UUID(as_uuid=False), nullable=True)
    current_rfq_id = Column(UUID(as_uuid=False), nullable=True)
    latest_report_version = Column(Integer, nullable=False, default=0)
    latest_strategy_version = Column(Integer, nullable=False, default=0)
    project_metadata = Column(JSONB, nullable=False, default=dict)
    analyzer_report = Column(JSONB, nullable=False, default=dict)
    strategy = Column(JSONB, nullable=False, default=dict)
    procurement_plan = Column(JSONB, nullable=False, default=dict)
    rfq_status = Column(Text, nullable=False, default="none")
    tracking_stage = Column(Text, nullable=False, default="init")
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
    def currency(self):
        return (self.project_metadata or {}).get("currency", "USD")

    @currency.setter
    def currency(self, value):
        if not self.project_metadata:
            self.project_metadata = {}
        self.project_metadata["currency"] = value or "USD"

    bom = relationship("BOM", foreign_keys=[bom_id])
    user = relationship("User", back_populates="projects")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")


class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = {"schema": "projects"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(Text, nullable=False)
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    actor_user_id = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    project = relationship("Project", back_populates="events")
