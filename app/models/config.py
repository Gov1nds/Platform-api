"""
Configuration, compliance, and flagged-JSON-promotion models.

Contract anchors
----------------
§2.77  Config_Version            — versioned system-configuration records
§2.78  Feature_Flag              — per-scope plan-tier / experiment flags
§2.79  DataSubjectRequest        — GDPR/CCPA right-to-erasure/access DSR workflow
§2.89  PII_Redaction_Rule        — rules for anonymising PII fields in logs
§2.90  Export_Control_Flag       — HS-code-level ITAR/EAR classification
§2.93  NormalizationTraceMerge   — join table replacing normalization_trace.merged_with_bom_line_ids UUID[]
§2.93  ConsolidationInsightLine  — join table replacing consolidation_insight.covered_bom_line_ids UUID[]
§2.93  DataSourcesSnapshotLink   — join table replacing data_sources_snapshot arrays

Design invariants
-----------------
* Config_Version is effectively APPEND-ONLY: old versions are kept for replay
  audit; only the latest non-deprecated row per config_type is authoritative.
* Feature_Flag rows are UPSERTED by the admin service.  ``updated_by`` is a
  required FK (non-nullable) so every flag change is attributable.
* DataSubjectRequest has a full state machine:
  RECEIVED → IN_PROGRESS → COMPLETED | REJECTED  (contract §3.19 / SM-016).
* The three join tables at the bottom replace UUID[] array columns that were
  explicitly flagged in §2.93 as 'should-be join tables' for referential
  integrity and queryability.

Domain
------
audit_observability_compliance + feature gating (§1.3)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_object,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    ConfigType,
    DataSourceLinkType,
    DataSubjectRequestState,
    DataSubjectRequestType,
    ExportControlClassification,
    FeatureFlagScope,
    PIIRedactionMethod,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# ConfigVersion  (§2.77)
# ─────────────────────────────────────────────────────────────────────────────


class ConfigVersion(Base, CreatedAtMixin):
    """Versioned system-configuration record.

    config_type CHECK: contract §3.80
      'nlp_model' | 'scoring_model' | 'weight_profile_defaults' |
      'approval_thresholds'

    Each (config_type, version) pair is unique.  The most-recent row with
    ``deprecated_at IS NULL`` is the live configuration for that type.

    Used by:
    * ``intelligence_orchestrator.py``   — nlp_model_version stamping
    * ``vendor_score_cache_service.py``  — scoring_model_version invalidation
    * ``rfq_service.py``                 — approval_thresholds lookup
    """

    __tablename__ = "config_version"

    # ── Primary key ──────────────────────────────────────────────────────────
    version_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Classification ────────────────────────────────────────────────────────
    config_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    effective_at: Mapped[datetime] = tstz()
    deprecated_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("config_type", values_of(ConfigType)),
        UniqueConstraint(
            "config_type",
            "version",
            name="uq_config_version_type_version",
        ),
        Index("ix_config_version_type_effective", "config_type", "effective_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# FeatureFlag  (§2.78)
# ─────────────────────────────────────────────────────────────────────────────


class FeatureFlag(Base, CreatedAtMixin):
    """Platform feature flag — global, org-scoped, or user-scoped.

    ``key`` is the stable identifier consumed by client code
    (e.g. ``"rfq.multi_vendor_split"``).

    ``scope`` CHECK: contract §3.63 ('global' | 'organization' | 'user').

    ``value_json`` encodes the flag value.  Boolean flags store
    ``{"enabled": true}``.  Gradual-rollout flags store
    ``{"percentage": 20, "cohort": "beta"}``.

    ``updated_by`` is NOT NULL — every flag change must be attributable to a
    platform admin.  The FK is ON DELETE RESTRICT to prevent losing
    accountability when a user is deactivated (deactivated users are soft-
    deleted via ``deleted_at``, not hard-deleted from the ``user`` table).
    """

    __tablename__ = "feature_flag"

    # ── Primary key ──────────────────────────────────────────────────────────
    flag_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Identity ──────────────────────────────────────────────────────────────
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)

    # ── Value ─────────────────────────────────────────────────────────────────
    value_json: Mapped[dict] = jsonb_object()

    # ── Provenance ────────────────────────────────────────────────────────────
    updated_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    updated_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("scope", values_of(FeatureFlagScope)),
        UniqueConstraint("key", name="uq_feature_flag_key"),
        Index("ix_feature_flag_scope", "scope"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DataSubjectRequest  (§2.79)
# ─────────────────────────────────────────────────────────────────────────────


class DataSubjectRequest(Base, CreatedAtMixin):
    """GDPR / CCPA data-subject rights request (DSR).

    State machine SM-016:  RECEIVED → IN_PROGRESS → COMPLETED | REJECTED
    (contract §3.19).

    request_type CHECK: contract §3.81
      'access' | 'rectify' | 'erase' | 'portability'

    On COMPLETED with request_type='access' or 'portability':
      ``export_artifact_url`` is populated with the signed S3 URL of the
      GDPR export bundle.

    On COMPLETED with request_type='erase':
      The compliance_service anonymises all user-linked PII fields in scope,
      writes an EventAuditLog entry, and leaves this row as the permanent
      compliance record.  The User row itself is soft-deleted.
    """

    __tablename__ = "data_subject_request"

    # ── Primary key ──────────────────────────────────────────────────────────
    request_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Subject ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT", index=True
    )

    # ── Request type ──────────────────────────────────────────────────────────
    request_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # ── State ─────────────────────────────────────────────────────────────────
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RECEIVED'")
    )

    # ── Artifact ──────────────────────────────────────────────────────────────
    export_artifact_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    received_at: Mapped[datetime] = tstz(default_now=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("request_type", values_of(DataSubjectRequestType)),
        enum_check("state", values_of(DataSubjectRequestState)),
        Index("ix_data_subject_request_state", "state", "received_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PIIRedactionRule  (§2.89)
# ─────────────────────────────────────────────────────────────────────────────


class PIIRedactionRule(Base, CreatedAtMixin):
    """Configurable PII redaction rule applied to log / audit fields.

    Consumed by ``compliance_service.py`` during DSR erase processing and by
    the nightly log-anonymisation job.

    ``field_path`` uses dot-notation, e.g.
    ``"event_audit_log.metadata_json.ip_hash"`` or
    ``"guest_search_log.search_query"``.

    redaction_method CHECK: contract §3.74 ('mask' | 'hash' | 'remove').
    """

    __tablename__ = "pii_redaction_rule"

    rule_id: Mapped[uuid.UUID] = uuid_pk()
    field_path: Mapped[str] = mapped_column(String(256), nullable=False)
    redaction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        enum_check("redaction_method", values_of(PIIRedactionMethod)),
        Index("ix_pii_redaction_rule_active", "active"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ExportControlFlag  (§2.90)
# ─────────────────────────────────────────────────────────────────────────────


class ExportControlFlag(Base, CreatedAtMixin):
    """HS-code-level ITAR / EAR classification record.

    Queried by ``rfq_service.py`` at RFQ creation time: if any BOM line's
    HS code maps to a row with ``classification != 'NONE'``, the RFQ is
    flagged and the user must confirm compliance before dispatch.

    classification CHECK: contract §3.75 ('ITAR' | 'EAR' | 'NONE').
    """

    __tablename__ = "export_control_flag"

    flag_id: Mapped[uuid.UUID] = uuid_pk()
    hs_code: Mapped[str] = mapped_column(String(12), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        enum_check("classification", values_of(ExportControlClassification)),
        UniqueConstraint("hs_code", name="uq_export_control_flag_hs_code"),
        Index("ix_export_control_flag_classification", "classification"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NormalizationTraceMerge  (§2.93 — join table)
# ─────────────────────────────────────────────────────────────────────────────


class NormalizationTraceMerge(Base, CreatedAtMixin):
    """Replaces ``normalization_trace.merged_with_bom_line_ids UUID[]``.

    Contract §2.93:
      > merged_with_bom_line_ids UUID[] FLAGGED — should be a join table
      > normalization_trace_merge(trace_id, bom_line_id).

    Each row asserts that trace ``trace_id`` merged the BOM line
    ``bom_line_id`` into the primary line for that trace.
    """

    __tablename__ = "normalization_trace_merge"

    # Composite PK — no surrogate needed; (trace, merged_line) is unique.
    trace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )
    bom_line_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )

    # created_at inherited

    __table_args__ = (
        PrimaryKeyConstraint("trace_id", "bom_line_id"),
        ForeignKeyConstraint(
            ["trace_id"],
            ["normalization_trace.trace_id"],
            ondelete="CASCADE",
            name="fk_normalization_trace_merge_trace",
        ),
        ForeignKeyConstraint(
            ["bom_line_id"],
            ["bom_line.bom_line_id"],
            ondelete="CASCADE",
            name="fk_normalization_trace_merge_bom_line",
        ),
        Index("ix_normalization_trace_merge_trace", "trace_id"),
        Index("ix_normalization_trace_merge_bom_line", "bom_line_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ConsolidationInsightLine  (§2.93 — join table)
# ─────────────────────────────────────────────────────────────────────────────


class ConsolidationInsightLine(Base, CreatedAtMixin):
    """Replaces ``consolidation_insight.covered_bom_line_ids UUID[]``.

    Contract §2.93:
      > covered_bom_line_ids UUID[] FLAGGED — should be a join table
      > consolidation_insight_line(insight_id, bom_line_id).
    """

    __tablename__ = "consolidation_insight_line"

    insight_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )
    bom_line_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("insight_id", "bom_line_id"),
        ForeignKeyConstraint(
            ["insight_id"],
            ["consolidation_insight.insight_id"],
            ondelete="CASCADE",
            name="fk_consolidation_insight_line_insight",
        ),
        ForeignKeyConstraint(
            ["bom_line_id"],
            ["bom_line.bom_line_id"],
            ondelete="CASCADE",
            name="fk_consolidation_insight_line_bom_line",
        ),
        Index("ix_consolidation_insight_line_insight", "insight_id"),
        Index("ix_consolidation_insight_line_bom_line", "bom_line_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DataSourcesSnapshotLink  (§2.93 — join table)
# ─────────────────────────────────────────────────────────────────────────────


class DataSourcesSnapshotLink(Base, CreatedAtMixin):
    """Replaces UUID[] arrays on ``data_sources_snapshot``.

    Contract §2.93:
      > baseline_price_ids and similar arrays UUID[] FLAGGED — should be a
      > join table data_sources_snapshot_link(snapshot_id, source_type, source_id).

    ``source_type`` identifies which market-data table the ``source_id`` FK
    belongs to ('baseline_price' | 'forex_rate' | 'tariff_rate' | 'logistics_rate').
    This is a polymorphic reference (no typed FK) — justified by §2.93.
    """

    __tablename__ = "data_sources_snapshot_link"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("snapshot_id", "source_type", "source_id"),
        ForeignKeyConstraint(
            ["snapshot_id"],
            ["data_sources_snapshot.snapshot_id"],
            ondelete="CASCADE",
            name="fk_data_sources_snapshot_link_snapshot",
        ),
        enum_check(
            "source_type",
            values_of(DataSourceLinkType),
        ),
        Index("ix_data_sources_snapshot_link_snapshot", "snapshot_id"),
        Index("ix_data_sources_snapshot_link_source", "source_type", "source_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "ConfigVersion",
    "FeatureFlag",
    "DataSubjectRequest",
    "PIIRedactionRule",
    "ExportControlFlag",
    "NormalizationTraceMerge",
    "ConsolidationInsightLine",
    "DataSourcesSnapshotLink",
]
