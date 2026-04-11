"""
Audit logging utility.

Writes immutable records to the Event_Audit_Log table for every
state machine transition, authorization denial, and security event.

The audit table is APPEND-ONLY: no UPDATE or DELETE operations are
permitted. All queries go through this module.

References: GAP-006, architecture.md CC-05, NFR-004, state-machines.md SMP-03
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.core.database import Base

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventAuditLog(Base):
    """
    Append-only audit log.

    Schema: ``ops.event_audit_log``
    """

    __tablename__ = "event_audit_log"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_actor", "actor_id"),
        Index("ix_audit_timestamp", "created_at"),
        Index("ix_audit_event_type", "event_type"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = Column(String(120), nullable=False)
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(80), nullable=False)
    actor_id = Column(String(80), nullable=True)
    actor_type = Column(String(40), nullable=False)  # USER | VENDOR | SYSTEM | CRON | ADMIN
    from_state = Column(String(60), nullable=True)
    to_state = Column(String(60), nullable=True)
    field_changes = Column(JSONB, nullable=False, default=dict)
    trace_id = Column(String(80), nullable=True)
    idempotency_key = Column(String(200), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


def audit_log(
    db: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str | None,
    actor_type: str,
    from_state: str | None = None,
    to_state: str | None = None,
    field_changes: dict[str, Any] | None = None,
    trace_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EventAuditLog:
    """
    Create an immutable audit record.

    This function only performs INSERT — never UPDATE or DELETE.
    The caller is responsible for committing the session.
    """
    entry = EventAuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        actor_type=actor_type,
        from_state=from_state,
        to_state=to_state,
        field_changes=field_changes or {},
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    db.add(entry)
    logger.debug(
        "Audit: %s %s/%s %s→%s actor=%s",
        event_type, entity_type, entity_id,
        from_state, to_state, actor_id,
    )
    return entry