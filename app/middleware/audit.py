"""
Audit logging utility.

Writes immutable records to the Event_Audit_Log table for every
state machine transition, authorization denial, and security event.

The audit table is APPEND-ONLY: no UPDATE or DELETE operations are
permitted.

References: GAP-006, architecture.md CC-05, NFR-004, state-machines.md SMP-03
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.events import EventAuditLog

logger = logging.getLogger(__name__)


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
    organization_id: str | None = None,
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
        organization_id=organization_id,
        payload=metadata or {},
    )
    db.add(entry)
    logger.debug(
        "Audit: %s %s/%s %s→%s actor=%s",
        event_type, entity_type, entity_id,
        from_state, to_state, actor_id,
    )
    return entry
