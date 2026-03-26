"""Project model — canonical application record for BOM + analysis + procurement."""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship

from app.core.database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(String(36), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    guest_session_id = Column(String(128), nullable=True, index=True)  # NEW
    name = Column(String(255), nullable=True)
    file_name = Column(String(255), nullable=True)
    status = Column(String(50), default="uploaded", index=True)
    total_parts = Column(Integer, default=0)
    recommended_location = Column(String(100), nullable=True)
    average_cost = Column(Float, nullable=True)
    cost_range_low = Column(Float, nullable=True)
    cost_range_high = Column(Float, nullable=True)
    savings_percent = Column(Float, nullable=True)
    lead_time = Column(Float, nullable=True)
    currency = Column(String(10), default="USD")  # NEW: track project currency
    decision_summary = Column(Text, nullable=True)
    analyzer_report = Column(JSON, nullable=True)
    strategy = Column(JSON, nullable=True)
    procurement_plan = Column(JSON, nullable=True)
    project_metadata = Column(JSON, nullable=True)
    rfq_status = Column(String(30), nullable=True)
    tracking_stage = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bom = relationship("BOM", back_populates="project", uselist=False)
    user = relationship("User", back_populates="projects")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")  # NEW


class ProjectEvent(Base):
    """NEW: Event log for project status changes."""
    __tablename__ = "project_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # status_change, rfq_created, quote_received, etc.
    old_status = Column(String(50), nullable=True)
    new_status = Column(String(50), nullable=True)
    payload = Column(JSON, nullable=True)
    actor_user_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="events")