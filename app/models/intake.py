"""Universal intake session models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from app.core.database import Base


class IntakeSession(Base):
    __tablename__ = "intake_sessions"
    __table_args__ = (
        Index("uq_intake_sessions_namespace_key", "namespace", "idempotency_key", unique=True),
        Index("ix_intake_sessions_user_id", "user_id"),
        Index("ix_intake_sessions_guest_session_id", "guest_session_id"),
        Index("ix_intake_sessions_status", "status"),
        Index("ix_intake_sessions_input_type", "input_type"),
        Index("ix_intake_sessions_project_id", "project_id"),
        {"schema": "projects"},
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    namespace = Column(String(80), nullable=False, default="intake.submit")
    idempotency_key = Column(String(120), nullable=False, default="")
    request_hash = Column(String(128), nullable=False, default="")

    user_id = Column(String(36), nullable=True, index=True)
    guest_session_id = Column(String(36), nullable=True, index=True)
    session_token = Column(String(120), nullable=True, index=True)

    input_type = Column(String(40), nullable=False, default="auto")
    intent = Column(String(40), nullable=False, default="auto")
    source_channel = Column(String(40), nullable=False, default="web")

    raw_input_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    voice_transcript = Column(Text, nullable=True)

    source_file_name = Column(Text, nullable=True)
    source_file_type = Column(String(40), nullable=True)
    source_file_size = Column(Integer, nullable=True)
    source_file_path = Column(Text, nullable=True)

    audio_file_name = Column(Text, nullable=True)
    audio_file_type = Column(String(80), nullable=True)
    audio_file_size = Column(Integer, nullable=True)
    audio_file_path = Column(Text, nullable=True)

    delivery_location = Column(String(120), nullable=True)
    target_currency = Column(String(20), nullable=True)
    priority = Column(String(20), nullable=True, default="cost")

    status = Column(String(40), nullable=False, default="received")
    parse_status = Column(String(40), nullable=False, default="pending")
    analysis_status = Column(String(40), nullable=False, default="pending")
    workflow_status = Column(String(40), nullable=False, default="received")

    confidence_score = Column(Float, nullable=False, default=0.0)
    warnings = Column(JSON, nullable=False, default=list)
    suggestions = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)
    parsed_payload = Column(JSON, nullable=False, default=dict)
    normalized_payload = Column(JSON, nullable=False, default=dict)
    analysis_payload = Column(JSON, nullable=False, default=dict)
    preview_payload = Column(JSON, nullable=False, default=dict)

    bom_id = Column(String(36), nullable=True, index=True)
    analysis_id = Column(String(36), nullable=True, index=True)
    project_id = Column(String(36), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = relationship(
        "IntakeItem",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class IntakeItem(Base):
    __tablename__ = "intake_items"
    __table_args__ = (
        Index("ix_intake_items_session_id", "session_id"),
        Index("ix_intake_items_category", "category"),
        Index("ix_intake_items_item_name", "item_name"),
        {"schema": "projects"},
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("projects.intake_sessions.id", ondelete="CASCADE"), nullable=False)

    line_no = Column(Integer, nullable=False, default=1)
    raw_text = Column(Text, nullable=False, default="")
    item_name = Column(Text, nullable=False, default="")
    category = Column(String(80), nullable=False, default="standard")
    material = Column(Text, nullable=True)
    process = Column(Text, nullable=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(30), nullable=True)
    specs = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=False, default=0.0)
    warnings = Column(JSON, nullable=False, default=list)
    source_payload = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("IntakeSession", back_populates="items")