"""
Event audit log, idempotency, platform event, and report snapshot models.

References: GAP-006, GAP-015, architecture.md CC-05, NFR-004,
            state-machines.md SMP-03, canonical-domain-model.md FCD-14
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, Integer, Date, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class PlatformEvent(Base):
    """Legacy event tracking — retained for backward compatibility."""
    __tablename__ = "platform_events"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=False), nullable=True)
    actor_type = Column(Text, nullable=False, default="user")
    resource_type = Column(Text, nullable=True)
    resource_id = Column(UUID(as_uuid=False), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)


class EventAuditLog(Base):
    """
    Append-only, immutable audit log. 7-year retention minimum.
    No updated_at. No deleted_at. Never updated or deleted.

    Reference: GAP-006, NFR-004, FCD-14
    """
    __tablename__ = "event_audit_log"
    __table_args__ = (
        Index("ix_eal_entity", "entity_type", "entity_id"),
        Index("ix_eal_actor", "actor_id"),
        Index("ix_eal_created", "created_at"),
        Index("ix_eal_type", "event_type"),
        Index("ix_eal_org", "organization_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = Column(String(120), nullable=False)
    entity_type = Column(String(80), nullable=True)
    entity_id = Column(UUID(as_uuid=False), nullable=True)
    actor_id = Column(UUID(as_uuid=False), nullable=True)
    actor_type = Column(String(40), nullable=False, default="SYSTEM")  # ActorType
    from_state = Column(String(40), nullable=True)
    to_state = Column(String(40), nullable=True)
    field_changes = Column(JSONB, nullable=False, default=dict)
    payload = Column(JSONB, nullable=False, default=dict)
    trace_id = Column(String(64), nullable=True)
    span_id = Column(String(32), nullable=True)
    idempotency_key = Column(String(120), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    # NO updated_at — append-only
    # NO deleted_at — 7-year retention, never deleted


class IdempotencyRecord(Base):
    """
    Idempotency key store (DB fallback; primary store is Redis).
    """
    __tablename__ = "idempotency_records"
    __table_args__ = {"schema": "ops"}

    key = Column(String(120), primary_key=True)
    response_body = Column(JSONB, nullable=False, default=dict)
    response_status = Column(Integer, nullable=False, default=200)
    created_at = Column(DateTime(timezone=True), default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)


class ReportSnapshot(Base):
    """
    Analytical report snapshot. Never overwritten — each run creates new record (FCD-15).
    """
    __tablename__ = "report_snapshots"
    __table_args__ = (
        Index("ix_rs_org", "organization_id"),
        Index("ix_rs_run", "report_run_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    report_type = Column(Text, nullable=False)
    scope_type = Column(Text, nullable=True)
    scope_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    report_run_id = Column(UUID(as_uuid=False), nullable=True)  # unique per computation
    snapshot_date = Column(Date, nullable=True)
    generated_by = Column(String(40), nullable=True)  # on_demand, scheduled, nightly
    version = Column(Integer, nullable=False, default=1)
    filters_json = Column(JSONB, nullable=False, default=dict)
    data_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)