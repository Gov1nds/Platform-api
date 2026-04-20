"""
Intelligence-layer entities — NLP normalization traces, scoring, strategy,
consolidation, evidence, and data-source snapshots.

Contract anchors
----------------
§2.9  Normalization_Run          §2.10 Normalization_Trace (APPEND-ONLY)
§2.11 Candidate_Match            §2.12 Review_Task
§2.23 Vendor_Filter_Result       §2.24 Vendor_Score_Cache
§2.25 Score_Breakdown            §2.26 Strategy_Recommendation
§2.27 Substitution_Recommendation §2.28 Consolidation_Insight
§2.29 Data_Sources_Snapshot      §2.30 Evidence_Record

State vocabularies
------------------
§3.16 Review_Task.status         §3.17 Freshness
§3.30 Normalization_Run.status   §3.42 Vendor_Score_Cache.confidence
§3.62 Evidence.data_point_type   §3.71 Vendor_Filter_Result.elimination_step
§3.72 Strategy.recommended_mode  §3.73 Normalization_Trace.decision_type

Conflict notes
--------------
* CN-15: ``normalization_trace.merged_with_bom_line_ids`` is **NOT** stored
  here; a ``normalization_trace_merge`` join table is authoritative
  (see ``config.py``).
* CN-16: ``consolidation_insight.covered_bom_line_ids`` is **NOT** stored
  here; a ``consolidation_insight_line`` join table is authoritative
  (see ``config.py``).
* CN-17: ``data_sources_snapshot``'s five UUID[] source arrays are **NOT**
  stored here; a ``data_sources_snapshot_link`` table is authoritative
  (see ``config.py``).
* CN-19: auto-commit at confidence ≥ 0.85; < 0.85 → review.
* CN-20: ``vendor_score_cache`` rows are authoritative for scoring;
  ``bom_line.score_cache_json`` is a denormalized read cache only.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_array,
    jsonb_object,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    EvidenceDataPointType,
    FreshnessStatus,
    NormalizationDecisionType,
    NormalizationRunStatus,
    ReviewTaskStatus,
    ScoreDimension,
    SourcingMode,
    VendorFilterEliminationStep,
    VendorScoreConfidence,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# NormalizationRun (§2.9)
# ─────────────────────────────────────────────────────────────────────────────


class NormalizationRun(Base):
    """One batch run of the NLP normalization pipeline. Each run processes
    one or more BOM_Line rows and stamps the ``nlp_model_version`` used."""

    __tablename__ = "normalization_run"

    run_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    nlp_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    output_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Domain-specific creation timestamp per contract section 2.9.
    started_at: Mapped[datetime] = tstz(default_now=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'QUEUED'")
    )

    __table_args__ = (
        enum_check("status", values_of(NormalizationRunStatus)),
        CheckConstraint("input_count >= 0", name="input_count_nonneg"),
        CheckConstraint("output_count >= 0", name="output_count_nonneg"),
        Index(
            "ix_normalization_run_project_id_started_at",
            "project_id",
            "started_at",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NormalizationTrace (§2.10) — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class NormalizationTrace(Base, CreatedAtMixin):
    """Append-only record of one normalization decision on one BOM_Line.

    ``merged_with_bom_line_ids`` is **not** persisted here — it lives in
    ``normalization_trace_merge`` (CN-15).
    """

    __tablename__ = "normalization_trace"

    trace_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    normalization_run_id: Mapped[uuid.UUID] = uuid_fk(
        "normalization_run.run_id", ondelete="RESTRICT"
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_output_json: Mapped[dict] = jsonb_object()
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(16), nullable=False)
    nlp_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    part_master_candidates_json: Mapped[list] = jsonb_array()
    ambiguity_flags: Mapped[list] = jsonb_array()
    split_from_bom_line_id: Mapped[uuid.UUID | None] = uuid_fk(
        "bom_line.bom_line_id", ondelete="SET NULL", nullable=True
    )
    part_id_match: Mapped[uuid.UUID | None] = uuid_fk(
        "part_master.part_id", ondelete="SET NULL", nullable=True
    )

    __table_args__ = (
        enum_check("decision_type", values_of(NormalizationDecisionType)),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        Index(
            "ix_normalization_trace_bom_line_id_created_at",
            "bom_line_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index(
            "ix_normalization_trace_normalization_run_id",
            "normalization_run_id",
        ),
        Index("ix_normalization_trace_part_id_match", "part_id_match"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CandidateMatch (§2.11)
# ─────────────────────────────────────────────────────────────────────────────


class CandidateMatch(Base, CreatedAtMixin):
    """Ranked candidate match between a BOM_Line and a Part_Master entry."""

    __tablename__ = "candidate_match"

    match_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    part_id: Mapped[uuid.UUID] = uuid_fk(
        "part_master.part_id", ondelete="RESTRICT"
    )
    similarity_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    match_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_distance: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6), nullable=True
    )
    token_overlap: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "similarity_score >= 0 AND similarity_score <= 1",
            name="similarity_score_range",
        ),
        CheckConstraint("match_rank >= 1", name="match_rank_positive"),
        Index(
            "ix_candidate_match_bom_line_id_match_rank",
            "bom_line_id",
            "match_rank",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ReviewTask (§2.12)
# ─────────────────────────────────────────────────────────────────────────────


class ReviewTask(Base, CreatedAtMixin):
    """Human-review task raised when normalization confidence < 0.85 (CN-19)
    or when ambiguity flags fire."""

    __tablename__ = "review_task"

    task_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    assigned_to: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    flags: Mapped[list] = jsonb_array()
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("status", values_of(ReviewTaskStatus)),
        Index("ix_review_task_status_created_at", "status", "created_at"),
        Index("ix_review_task_assigned_to_status", "assigned_to", "status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorFilterResult (§2.23)
# ─────────────────────────────────────────────────────────────────────────────


class VendorFilterResult(Base, CreatedAtMixin):
    """Records every vendor eliminated from a BOM_Line shortlist along with
    the step that eliminated them (hard filter or technical-fit below
    threshold)."""

    __tablename__ = "vendor_filter_result"

    result_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    elimination_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    elimination_step: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        enum_check("elimination_step", values_of(VendorFilterEliminationStep)),
        Index("ix_vendor_filter_result_bom_line_id", "bom_line_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorScoreCache (§2.24)  — authoritative scoring rows (CN-20)
# ─────────────────────────────────────────────────────────────────────────────


class VendorScoreCache(Base, CreatedAtMixin):
    """Per (BOM_Line × Vendor × weight_profile × market_context) score row.

    Rows have an explicit TTL via ``ttl_expires_at`` (default 6 hours — see
    §12.1). The uniqueness constraint enforces cache idempotency.
    """

    __tablename__ = "vendor_score_cache"

    cache_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    total_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    market_context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    breakdown_json: Mapped[dict] = jsonb_object()
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    data_sources_snapshot_id: Mapped[uuid.UUID] = uuid_fk(
        "data_sources_snapshot.snapshot_id",
        ondelete="RESTRICT",
        use_alter=True,
        name="fk_vendor_score_cache_data_sources_snapshot_id",
    )
    scoring_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ttl_expires_at: Mapped[datetime] = tstz()

    __table_args__ = (
        enum_check("confidence", values_of(VendorScoreConfidence)),
        # Repo B scores must be clamped to [0.000, 100.000] and rounded to
        # three decimals before insert to avoid floating-point edge overflow.
        CheckConstraint(
            "total_score >= 0 AND total_score <= 100",
            name="total_score_range",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint(
            "char_length(weight_profile_hash) = 64",
            name="weight_profile_hash_sha256_hex",
        ),
        CheckConstraint(
            "char_length(market_context_hash) = 64",
            name="market_context_hash_sha256_hex",
        ),
        UniqueConstraint(
            "bom_line_id",
            "vendor_id",
            "weight_profile_hash",
            "market_context_hash",
            name="uq_vendor_score_cache_composite",
        ),
        Index("ix_vendor_score_cache_ttl_expires_at", "ttl_expires_at"),
        Index(
            "ix_vendor_score_cache_bom_line_id_rank",
            "bom_line_id",
            "rank",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ScoreBreakdown (§2.25) — optional per-dimension denormalization
# ─────────────────────────────────────────────────────────────────────────────


class ScoreBreakdown(Base, CreatedAtMixin):
    """Optional per-dimension row (``breakdown_json`` remains authoritative).

    Dimensions are the 5 scoring axes from the Repo B engine:
    cost_competitiveness, lead_time_availability, quality_reliability,
    strategic_fit, operational_capability.
    """

    __tablename__ = "score_breakdown"

    breakdown_id: Mapped[uuid.UUID] = uuid_pk()
    cache_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor_score_cache.cache_id", ondelete="CASCADE"
    )
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    # Clamp and round Repo B score values to [0.000, 100.000] before insert.
    score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    # Clamp and round Repo B weighted contributions to [0.000, 100.000].
    weighted_contribution: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False
    )
    reasons_json: Mapped[list] = jsonb_array()

    __table_args__ = (
        enum_check("dimension", values_of(ScoreDimension)),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        CheckConstraint("weight >= 0 AND weight <= 1", name="weight_range"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# StrategyRecommendation (§2.26)
# ─────────────────────────────────────────────────────────────────────────────


class StrategyRecommendation(Base, CreatedAtMixin):
    """Per BOM_Line sourcing-mode recommendation + TLC breakdown."""

    __tablename__ = "strategy_recommendation"

    recommendation_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    recommended_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    tlc_breakdown_json: Mapped[dict] = jsonb_object()
    q_break: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        enum_check("recommended_mode", values_of(SourcingMode)),
        Index(
            "ix_strategy_recommendation_bom_line_id",
            "bom_line_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SubstitutionRecommendation (§2.27)
# ─────────────────────────────────────────────────────────────────────────────


class SubstitutionRecommendation(Base, CreatedAtMixin):
    """Suggested alternative Part_Master entry for a BOM_Line."""

    __tablename__ = "substitution_recommendation"

    substitution_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    suggested_part_id: Mapped[uuid.UUID] = uuid_fk(
        "part_master.part_id", ondelete="RESTRICT"
    )
    spec_diff_json: Mapped[dict] = jsonb_object()
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        Index(
            "ix_substitution_recommendation_bom_line_id",
            "bom_line_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ConsolidationInsight (§2.28)  — covered lines live in join table (CN-16)
# ─────────────────────────────────────────────────────────────────────────────


class ConsolidationInsight(Base, CreatedAtMixin):
    """Cross-line consolidation recommendation.

    Coverage is modelled via ``consolidation_insight_line(insight_id,
    bom_line_id)`` (see ``config.py``); the ``covered_bom_line_ids`` array
    is deliberately absent here per CN-16.
    """

    __tablename__ = "consolidation_insight"

    insight_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="CASCADE"
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    estimated_savings: Mapped[Decimal] = money_default_zero()
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_consolidation_insight_project_id", "project_id"),
        Index("ix_consolidation_insight_vendor_id", "vendor_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DataSourcesSnapshot (§2.29) — sources live in link table (CN-17)
# ─────────────────────────────────────────────────────────────────────────────


class DataSourcesSnapshot(Base, CreatedAtMixin):
    """Immutable lineage record — which external-data rows were used in a
    given scoring / enrichment computation.

    Source UUIDs are stored via ``data_sources_snapshot_link`` (see
    ``config.py``) — the five UUID[] columns from the original spec are
    **not** persisted here per CN-17.
    """

    __tablename__ = "data_sources_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    scoring_cache_id: Mapped[uuid.UUID | None] = uuid_fk(
        "vendor_score_cache.cache_id",
        ondelete="SET NULL",
        nullable=True,
        use_alter=True,
        name="fk_data_sources_snapshot_scoring_cache_id",
    )
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    fetched_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        Index(
            "ix_data_sources_snapshot_bom_line_id",
            "bom_line_id",
        ),
        Index(
            "ix_data_sources_snapshot_scoring_cache_id",
            "scoring_cache_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# EvidenceRecord (§2.30)
# ─────────────────────────────────────────────────────────────────────────────


class EvidenceRecord(Base, CreatedAtMixin):
    """A single piece of evidence (price, lead-time, tariff, freight,
    performance, certification, forex) attached to a BOM_Line with its own
    freshness state."""

    __tablename__ = "evidence_record"

    evidence_id: Mapped[uuid.UUID] = uuid_pk()
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="CASCADE"
    )
    data_point_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[dict] = jsonb_object()
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = tstz(default_now=True)
    freshness_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'FRESH'")
    )

    __table_args__ = (
        enum_check("data_point_type", values_of(EvidenceDataPointType)),
        enum_check("freshness_status", values_of(FreshnessStatus)),
        Index(
            "ix_evidence_record_bom_line_id_data_point_type",
            "bom_line_id",
            "data_point_type",
        ),
        Index("ix_evidence_record_freshness_status", "freshness_status"),
    )


__all__ = [
    "NormalizationRun",
    "NormalizationTrace",
    "CandidateMatch",
    "ReviewTask",
    "VendorFilterResult",
    "VendorScoreCache",
    "ScoreBreakdown",
    "StrategyRecommendation",
    "SubstitutionRecommendation",
    "ConsolidationInsight",
    "DataSourcesSnapshot",
    "EvidenceRecord",
]
