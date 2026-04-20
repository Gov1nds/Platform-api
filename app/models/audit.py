"""
Audit, observability, and compliance persistence models.

Contract anchors
----------------
§2.74  Event_Audit_Log     — append-only immutable audit trail (7-year retention)
§2.75  Data_Freshness_Log  — per-record freshness tracking (market data)
§2.76  Integration_Run_Log — external-API call log with circuit-breaker state

Design invariants
-----------------
* Event_Audit_Log is APPEND-ONLY.  No UPDATE or DELETE is ever issued against
  this table.  The service layer (audit_service.py) enforces this with a
  context-manager guard and a DB-level row-security policy in production.
* Data_Freshness_Log has NO updated_at / deleted_at — it is also append-only:
  each refresh attempt creates a new row; the most-recent row per record_id is
  the authoritative state.
* Integration_Run_Log has no FK to any entity table intentionally — integration
  targets are heterogeneous external systems, not Repo C rows.
* All three tables carry only ``created_at`` (via CreatedAtMixin), not
  ``updated_at`` / ``deleted_at``, because they are event ledgers.

Domain
------
audit_observability_compliance (§1.3, §3.65–§3.66, §3.38)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_object,
    jsonb_object_nullable,
    tstz,
    uuid_fk,
    uuid_pk,
    uuid_polymorphic,
)
from app.models.enums import (
    EventActorType,
    FreshnessLogStatus,
    IntegrationCircuitState,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# EventAuditLog  (§2.74)
# ─────────────────────────────────────────────────────────────────────────────


class EventAuditLog(Base, CreatedAtMixin):
    """Immutable, append-only record of every state transition and mutation.

    Retention: minimum 7 years (contract §2.74).

    Indexed for:
      * Per-entity history:         (entity_type, entity_id)
      * Per-user timeline:          (user_id, created_at)
      * Event-type time-series:     (event_type, created_at)
      * Distributed trace look-up:  (correlation_id)
      * Global time-range scans:    (created_at)

    ``before_state_json`` / ``after_state_json`` carry a minimal diff of the
    mutated fields only — not the entire row — to keep storage proportional.
    ``metadata_json`` holds call-site context (endpoint, service, correlation).

    actor_type CHECK: contract §3.65 ('user' | 'system' | 'vendor' | 'admin').
    """

    __tablename__ = "event_audit_log"

    # ── Primary key ──────────────────────────────────────────────────────────
    event_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Core classification ───────────────────────────────────────────────────
    # e.g. "bom_line.status_changed", "purchase_order.sent", "user.deleted"
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # e.g. "bom_line", "purchase_order", "user"
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()

    # ── Actor ────────────────────────────────────────────────────────────────
    # Nullable because system jobs may act without a User context.
    user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # ── Payload ───────────────────────────────────────────────────────────────
    before_state_json: Mapped[dict] = jsonb_object()
    after_state_json: Mapped[dict] = jsonb_object()
    # Carries: endpoint, ip_hash, correlation_id (mirrored), user_agent snippet
    metadata_json: Mapped[dict] = jsonb_object()

    # ── Distributed-tracing IDs (OpenTelemetry W3C) ───────────────────────────
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # created_at inherited from CreatedAtMixin

    __table_args__ = (
        enum_check("actor_type", values_of(EventActorType)),
        # ── Access-pattern-driven indexes ─────────────────────────────────────
        Index("ix_event_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_event_audit_log_user_time", "user_id", "created_at"),
        Index("ix_event_audit_log_type_time", "event_type", "created_at"),
        Index("ix_event_audit_log_correlation_id", "correlation_id"),
        Index("ix_event_audit_log_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DataFreshnessLog  (§2.75)
# ─────────────────────────────────────────────────────────────────────────────


class DataFreshnessLog(Base, CreatedAtMixin):
    """Per-attempt freshness record for every market-data row.

    Written by ``freshness_service.py`` on every scheduled refresh, on-demand
    re-fetch, and stale-while-revalidate background retry.

    Design notes
    ------------
    * Append-only — each fetch attempt is a new row.
    * ``record_id`` is NOT a typed FK: the referenced row may live in
      ``baseline_price``, ``forex_rate``, ``tariff_rate``, or
      ``logistics_rate`` depending on ``table_name``.  A typed FK would
      require polymorphic infrastructure not warranted by a log table.
    * ``fetched_at`` is the primary sort key (not a generic created_at).
    * ``status`` CHECK: contract §3.66 ('success' | 'error' | 'stale').
    """

    __tablename__ = "data_freshness_log"

    # ── Primary key ──────────────────────────────────────────────────────────
    log_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Target record ─────────────────────────────────────────────────────────
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[uuid.UUID] = uuid_polymorphic()

    # ── Source ────────────────────────────────────────────────────────────────
    source_api: Mapped[str] = mapped_column(String(128), nullable=False)

    # ── Outcome ───────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    previous_value_json: Mapped[dict | None] = jsonb_object_nullable()
    new_value_json: Mapped[dict | None] = jsonb_object_nullable()
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    fetched_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("status", values_of(FreshnessLogStatus)),
        Index(
            "ix_data_freshness_log_record",
            "table_name",
            "record_id",
            "fetched_at",
            postgresql_ops={"fetched_at": "DESC"},
        ),
        Index(
            "ix_data_freshness_log_status_time",
            "status",
            "fetched_at",
            postgresql_ops={"fetched_at": "DESC"},
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# IntegrationRunLog  (§2.76)
# ─────────────────────────────────────────────────────────────────────────────


class IntegrationRunLog(Base):
    """One outbound API call to any external integration.

    Written by ``integrations/base_client.py`` for every attempt.  Used by:
      * Ops dashboards (circuit-breaker state overview)
      * Incident investigation (latency spikes, retry storms)
      * SLA evidence for SOC 2

    circuit_state CHECK: contract §3.38 ('CLOSED' | 'OPEN' | 'HALF_OPEN').

    ``request_hash`` is the SHA-256 of the serialised request payload and is
    the idempotency anchor for retry de-duplication.  It is NOT a FK.
    """

    __tablename__ = "integration_run_log"

    # ── Primary key ──────────────────────────────────────────────────────────
    run_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Target ────────────────────────────────────────────────────────────────
    # e.g. "digikey", "mouser", "dhl", "sendgrid", "repo_b"
    integration_target: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "get_part_price", "book_shipment", "normalize"
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── Outcome ───────────────────────────────────────────────────────────────
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    circuit_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'CLOSED'")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    started_at: Mapped[datetime] = tstz(default_now=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("circuit_state", values_of(IntegrationCircuitState)),
        Index(
            "ix_integration_run_log_target_op",
            "integration_target",
            "operation",
            "started_at",
        ),
        Index("ix_integration_run_log_circuit_state", "circuit_state", "started_at"),
        Index("ix_integration_run_log_request_hash", "request_hash"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "EventAuditLog",
    "DataFreshnessLog",
    "IntegrationRunLog",
]
