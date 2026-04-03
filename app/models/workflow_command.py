"""Workflow command ledger — idempotency + request/response audit envelope."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


class WorkflowCommand(Base):
    __tablename__ = "workflow_commands"
    __table_args__ = (
        Index(
            "uq_workflow_commands_namespace_key",
            "namespace",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_workflow_commands_project_id", "project_id"),
        Index("ix_workflow_commands_user_id", "user_id"),
        Index("ix_workflow_commands_status", "status"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    namespace = Column(Text, nullable=False)
    idempotency_key = Column(Text, nullable=False)
    payload_hash = Column(Text, nullable=False)
    request_method = Column(Text, nullable=False)
    request_path = Column(Text, nullable=False)
    user_id = Column(UUID(as_uuid=False), nullable=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    related_id = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="processing")  # processing | completed | failed
    response_json = Column(JSONB, nullable=False, default=dict)
    error_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)