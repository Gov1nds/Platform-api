"""Project model — canonical operational record for a BOM journey."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class ProjectWorkflowStage(str, enum.Enum):
    draft = "draft"
    guest_preview = "guest_preview"
    project_hydrated = "project_hydrated"
    strategy = "strategy"
    vendor_match = "vendor_match"
    rfq_pending = "rfq_pending"
    rfq_sent = "rfq_sent"
    quote_compare = "quote_compare"
    negotiation = "negotiation"
    vendor_selected = "vendor_selected"
    po_issued = "po_issued"
    in_production = "in_production"
    qc_inspection = "qc_inspection"
    shipped = "shipped"
    delivered = "delivered"
    spend_recorded = "spend_recorded"
    completed = "completed"
    cancelled = "cancelled"
    error = "error"


class ProjectVisibilityLevel(str, enum.Enum):
    private = "private"
    preview = "preview"
    full = "full"
    admin = "admin"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_guest_session", "guest_session_id"),
        Index("ix_projects_status", "status"),
        Index("ix_projects_analysis_status", "analysis_status"),
        Index("ix_projects_visibility_level", "visibility_level"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)

    name = Column(Text, nullable=False, default="Uploaded BOM")
    file_name = Column(Text, nullable=True)

    # Unified project state
    # H-5: `status` is the canonical workflow state column.
    #       `workflow_stage` is kept in sync by callers and serialize functions.
    #       Both columns exist in DB for backward compat — always write both.
    status = Column(Text, nullable=False, default=ProjectWorkflowStage.draft.value)
    workflow_stage = Column(Text, nullable=False, default=ProjectWorkflowStage.draft.value)
    # H-5: `visibility_level` is the canonical visibility column.
    #       `visibility` is kept in sync — always write both.
    visibility = Column(Text, nullable=False, default=ProjectVisibilityLevel.private.value)
    visibility_level = Column(Text, nullable=False, default=ProjectVisibilityLevel.private.value)

    def set_workflow_status(self, value):
        """H-5: Helper to write both status and workflow_stage atomically."""
        self.status = value
        self.workflow_stage = value

    def set_visibility(self, value):
        """H-5: Helper to write both visibility and visibility_level atomically."""
        self.visibility = value
        self.visibility_level = value

    analysis_status = Column(Text, nullable=False, default="guest_preview")
    report_visibility_level = Column(Text, nullable=False, default="preview")
    unlock_status = Column(Text, nullable=False, default="locked")
    workspace_route = Column(Text, nullable=True)

    total_parts = Column(Integer, nullable=False, default=0)
    recommended_location = Column(Text, nullable=True)
    average_cost = Column(Numeric(18, 6), nullable=True)
    cost_range_low = Column(Numeric(18, 6), nullable=True)
    cost_range_high = Column(Numeric(18, 6), nullable=True)
    savings_percent = Column(Numeric(12, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    decision_summary = Column(Text, nullable=True)

    # Canonical workflow pointers
    current_analysis_id = Column(UUID(as_uuid=False), nullable=True)
    current_strategy_run_id = Column(UUID(as_uuid=False), nullable=True)
    current_vendor_match_id = Column(UUID(as_uuid=False), nullable=True)
    current_rfq_id = Column(UUID(as_uuid=False), nullable=True)
    current_quote_id = Column(UUID(as_uuid=False), nullable=True)
    current_po_id = Column(UUID(as_uuid=False), nullable=True)
    current_shipment_id = Column(UUID(as_uuid=False), nullable=True)
    current_invoice_id = Column(UUID(as_uuid=False), nullable=True)

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

    @property
    def owner_user_id(self):
        return self.user_id

    @owner_user_id.setter
    def owner_user_id(self, value):
        self.user_id = value
    
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
    participants = relationship("ProjectParticipant", back_populates="project", cascade="all, delete-orphan")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")
    fulfillment_events = relationship("FulfillmentEvent", back_populates="project", cascade="all, delete-orphan")

    def _metadata_dict(self) -> dict:
     meta = self.project_metadata or {}
     return meta if isinstance(meta, dict) else {}

    @property
    def current_rfq_batch_id(self):
        return self.current_rfq_id

    @current_rfq_batch_id.setter
    def current_rfq_batch_id(self, value):
        self.current_rfq_id = value

    @property
    def current_vendor_id(self):
        meta = self._metadata_dict()
        return (
            meta.get("current_vendor_id")
            or meta.get("selected_vendor_id")
            or (str(self.current_vendor_match_id) if self.current_vendor_match_id else None)
        )

    @current_vendor_id.setter
    def current_vendor_id(self, value):
        if not self.project_metadata:
            self.project_metadata = {}
        self.project_metadata["current_vendor_id"] = value
        self.project_metadata["selected_vendor_id"] = value

    @property
    def selected_vendor_id(self):
        return self.current_vendor_id

    @selected_vendor_id.setter
    def selected_vendor_id(self, value):
        self.current_vendor_id = value


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