"""
audit.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Audit, Observability & Compliance Schema Layer

CONTRACT AUTHORITY: contract.md §2.74–2.76 (Event_Audit_Log, Data_Freshness_Log,
Integration_Run_Log), §2.79 (DataSubjectRequest), §2.89–2.90 (PIIRedactionRule,
ExportControlFlag), §3.19 (SM-016 DataSubjectRequest), §4.13 (Admin endpoints).

Invariants:
  • Event_Audit_Log is APPEND-ONLY: no UPDATE, no DELETE (§2.74).
  • Retention: minimum 7 years for Event_Audit_Log.
  • Telemetry (observability) and Business Events (audit) are SEPARATE streams
    with SEPARATE retention (requirements.yaml event_system principles).
  • DataSubjectRequest.state SM-016: RECEIVED → IN_PROGRESS → COMPLETED | REJECTED.
  • actor_type: user | system | vendor | admin (§3.65).
  • GDPR right_to_erasure: anonymizes PII fields; retains anonymized row for audit.
  • Export control flag checked at RFQ creation for ITAR/EAR HS codes.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import (
    EventActorType,
    IntegrationCircuitState,
    FreshnessLogStatus,
    DataSubjectRequestState,
    DataSubjectRequestType,
    ExportControlClassification,
    PGIBase,
    PIIRedactionMethod,
)


# ──────────────────────────────────────────────────────────────────────────
# Event_Audit_Log (contract §2.74)
# ──────────────────────────────────────────────────────────────────────────

class EventAuditLogSchema(PGIBase):
    """An append-only audit log entry for every state transition and mutation.

    No UPDATE, no DELETE ever — only INSERT.
    Retention: minimum 7 years.
    Correlation IDs propagated: Repo A → Repo C → Repo B → Event_Audit_Log.
    """

    event_id: UUID
    event_type: str = Field(max_length=128)
    entity_type: str = Field(max_length=64)
    entity_id: UUID
    user_id: Optional[UUID] = None
    actor_type: EventActorType
    before_state_json: dict[str, Any] = Field(default_factory=dict)
    after_state_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = Field(default=None, max_length=64)
    trace_id: Optional[str] = Field(default=None, max_length=64)
    span_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime


class EventAuditLogListResponse(PGIBase):
    """Paginated audit log (GET /api/v1/admin/audit-log)."""

    items: list[EventAuditLogSchema]
    next_cursor: Optional[str] = None


class EventAuditLogQueryParams(PGIBase):
    """Query parameters for the audit log endpoint."""

    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    actor_type: Optional[EventActorType] = None
    event_type: Optional[str] = None
    from_dt: Optional[datetime] = None
    to_dt: Optional[datetime] = None
    cursor: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)


# ──────────────────────────────────────────────────────────────────────────
# Data_Freshness_Log (contract §2.75) — also in market_data.py; full schema here
# ──────────────────────────────────────────────────────────────────────────

class DataFreshnessLogAdminSchema(PGIBase):
    """Full Data_Freshness_Log entry for admin inspection.

    Shared domain between data_freshness_and_market_context and
    audit_observability_compliance (requirements.yaml §2.75).
    """

    log_id: UUID
    table_name: str = Field(max_length=64)
    record_id: UUID
    source_api: str = Field(max_length=128)
    status: FreshnessLogStatus
    previous_value_json: Optional[dict[str, Any]] = None
    new_value_json: Optional[dict[str, Any]] = None
    fetched_at: datetime
    error_message: Optional[str] = None


class FreshnessLogListResponse(PGIBase):
    """Paginated freshness log (GET /api/v1/admin/freshness-log)."""

    items: list[DataFreshnessLogAdminSchema]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Integration_Run_Log (contract §2.76)
# ──────────────────────────────────────────────────────────────────────────

class IntegrationRunLogSchema(PGIBase):
    """Log of every external API call made by Repo C.

    request_hash: SHA-256 of the request to detect duplicates.
    circuit_state: CLOSED (healthy) | OPEN (failing) | HALF_OPEN (testing).
    """

    run_id: UUID
    integration_target: str = Field(max_length=64)
    operation: str = Field(max_length=64)
    request_hash: str = Field(max_length=64)
    response_status: Optional[int] = None
    latency_ms: Optional[int] = None
    circuit_state: IntegrationCircuitState
    retry_count: int = 0
    started_at: datetime
    completed_at: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────
# DataSubjectRequest (contract §2.79)
# ──────────────────────────────────────────────────────────────────────────

class DataSubjectRequestSchema(PGIBase):
    """A GDPR / CCPA data subject rights request (SM-016).

    state: RECEIVED → IN_PROGRESS → COMPLETED | REJECTED.
    export_artifact_url: presigned S3 URL for 'access' and 'portability' requests.
    """

    request_id: UUID
    user_id: UUID
    request_type: DataSubjectRequestType
    state: DataSubjectRequestState
    export_artifact_url: Optional[str] = Field(default=None, max_length=1024)
    received_at: datetime
    completed_at: Optional[datetime] = None


class DataSubjectRequestCreateRequest(PGIBase):
    """POST /api/v1/admin/data-subject-requests."""

    user_id: UUID
    request_type: DataSubjectRequestType


class DataSubjectRequestCreateResponse(PGIBase):
    """Response after creating a data subject request."""

    request_id: UUID


class DataSubjectRequestUpdateRequest(PGIBase):
    """Admin updates the state of a data subject request."""

    state: DataSubjectRequestState
    export_artifact_url: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# PII_Redaction_Rule (contract §2.89)
# ──────────────────────────────────────────────────────────────────────────

class PIIRedactionRuleSchema(PGIBase):
    """Rule specifying how PII in a field path should be redacted in logs.

    field_path: JSONPath-style path (e.g. 'user.email', 'guest_session.session_token').
    redaction_method: mask | hash | remove.
    """

    rule_id: UUID
    field_path: str = Field(max_length=256)
    redaction_method: PIIRedactionMethod
    active: bool = True


class PIIRedactionRuleCreateRequest(PGIBase):
    """Create a PII redaction rule."""

    field_path: str = Field(min_length=1, max_length=256)
    redaction_method: PIIRedactionMethod
    active: bool = True


# ──────────────────────────────────────────────────────────────────────────
# Export_Control_Flag (contract §2.90)
# ──────────────────────────────────────────────────────────────────────────

class ExportControlFlagSchema(PGIBase):
    """ITAR/EAR export control classification for a specific HS code.

    Checked at RFQ creation; buyer must confirm when requires_confirmation=True.
    classification: ITAR | EAR | NONE.
    """

    flag_id: UUID
    hs_code: str = Field(max_length=12)
    classification: ExportControlClassification
    requires_confirmation: bool = True


class ExportControlFlagCreateRequest(PGIBase):
    """Create or update an export control flag for an HS code."""

    hs_code: str = Field(min_length=4, max_length=12)
    classification: ExportControlClassification
    requires_confirmation: bool = True