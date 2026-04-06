import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Numeric, Boolean, Float, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base

def _now(): return datetime.now(timezone.utc)
def _uuid(): return str(uuid.uuid4())

WORKFLOW_STAGES = [
    "draft","analyzing","analyzed","strategy","vendor_match",
    "rfq_pending","rfq_sent","quote_compare","negotiation",
    "vendor_selected","po_issued","in_production","qc_inspection",
    "shipped","delivered","completed","cancelled",
]

class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id","user_id"),
        Index("ix_projects_guest_session","guest_session_id"),
        {"schema":"projects"},
    )
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    sourcing_case_id = Column(UUID(as_uuid=False), nullable=True)
    name = Column(Text, nullable=False, default="Uploaded BOM")
    file_name = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="draft")
    visibility = Column(Text, nullable=False, default="owner_only")
    total_parts = Column(Integer, nullable=False, default=0)
    average_cost = Column(Numeric(18,6), nullable=True)
    cost_range_low = Column(Numeric(18,6), nullable=True)
    cost_range_high = Column(Numeric(18,6), nullable=True)
    lead_time_days = Column(Numeric(12,2), nullable=True)
    decision_summary = Column(Text, nullable=True)
    current_rfq_id = Column(UUID(as_uuid=False), nullable=True)
    current_po_id = Column(UUID(as_uuid=False), nullable=True)
    analyzer_report = Column(JSONB, nullable=False, default=dict)
    strategy = Column(JSONB, nullable=False, default=dict)
    project_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    user = relationship("User", back_populates="projects")
    events = relationship("ProjectEvent", back_populates="project", cascade="all, delete-orphan")
    acl_entries = relationship("ProjectACL", back_populates="project", cascade="all, delete-orphan")

class ProjectACL(Base):
    __tablename__ = "project_acl"
    __table_args__ = (
        Index("ix_pacl_project","project_id"),
        Index("ix_pacl_principal","principal_type","principal_id"),
        {"schema":"projects"},
    )
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    principal_type = Column(Text, nullable=False)
    principal_id = Column(UUID(as_uuid=False), nullable=False)
    role = Column(Text, nullable=False, default="viewer")
    granted_at = Column(DateTime(timezone=True), default=_now)
    project = relationship("Project", back_populates="acl_entries")

class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = {"schema":"projects"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(Text, nullable=False)
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    actor_user_id = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    project = relationship("Project", back_populates="events")

class SearchSession(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (
        Index("ix_ss_user","user_id"),
        Index("ix_ss_guest","guest_session_id"),
        {"schema":"projects"},
    )
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    session_token = Column(String(120), nullable=True)
    query_text = Column(Text, nullable=True)
    query_type = Column(String(40), nullable=False, default="component")
    input_type = Column(String(40), nullable=False, default="text")
    delivery_location = Column(Text, nullable=True)
    target_currency = Column(String(10), nullable=True, default="USD")
    results_json = Column(JSONB, nullable=False, default=dict)
    analysis_payload = Column(JSONB, nullable=False, default=dict)
    promoted_to = Column(String(40), nullable=True)
    promoted_to_id = Column(UUID(as_uuid=False), nullable=True)
    status = Column(String(40), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

class SourcingCase(Base):
    __tablename__ = "sourcing_cases"
    __table_args__ = (
        Index("ix_sc_user","user_id"),
        Index("ix_sc_guest","guest_session_id"),
        {"schema":"projects"},
    )
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), nullable=True)
    session_token = Column(String(120), nullable=True)
    search_session_id = Column(UUID(as_uuid=False), nullable=True)
    name = Column(Text, nullable=False, default="Saved search")
    query_text = Column(Text, nullable=True)
    analysis_payload = Column(JSONB, nullable=False, default=dict)
    vendor_shortlist = Column(JSONB, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    promoted_to_project_id = Column(UUID(as_uuid=False), nullable=True)
    status = Column(String(40), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

class IntakeSession(Base):
    __tablename__ = "intake_sessions"
    __table_args__ = (
        Index("ix_intake_user","user_id"),
        Index("ix_intake_token","session_token"),
        {"schema":"projects"},
    )
    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)
    guest_session_id = Column(String(36), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    session_token = Column(String(120), nullable=True)
    input_type = Column(String(40), nullable=False, default="auto")
    raw_input_text = Column(Text, nullable=True)
    source_file_name = Column(Text, nullable=True)
    source_file_path = Column(Text, nullable=True)
    delivery_location = Column(String(120), nullable=True)
    target_currency = Column(String(20), nullable=True)
    priority = Column(String(20), nullable=True, default="cost")
    item_count = Column(Integer, nullable=False, default=0)
    recommended_flow = Column(String(40), nullable=False, default="search_session")
    status = Column(String(40), nullable=False, default="received")
    parsed_payload = Column(JSONB, nullable=False, default=dict)
    analysis_payload = Column(JSONB, nullable=False, default=dict)
    bom_id = Column(String(36), nullable=True)
    project_id = Column(String(36), nullable=True)
    search_session_id = Column(String(36), nullable=True)
    sourcing_case_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    items = relationship("IntakeItem", back_populates="session", cascade="all, delete-orphan")

class IntakeItem(Base):
    __tablename__ = "intake_items"
    __table_args__ = (Index("ix_ii_session","session_id"),{"schema":"projects"})
    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(String(36), ForeignKey("projects.intake_sessions.id", ondelete="CASCADE"), nullable=False)
    line_no = Column(Integer, nullable=False, default=1)
    raw_text = Column(Text, nullable=False, default="")
    item_name = Column(Text, nullable=False, default="")
    category = Column(String(80), nullable=False, default="standard")
    material = Column(Text, nullable=True)
    process = Column(Text, nullable=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(30), nullable=True)
    specs = Column(JSONB, nullable=False, default=dict)
    confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), default=_now)
    session = relationship("IntakeSession", back_populates="items")
