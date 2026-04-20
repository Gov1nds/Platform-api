"""
intelligence.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Intelligence Orchestration Schema Layer (Repo C side)

CONTRACT AUTHORITY: contract.md §2.9–2.12 (NormRun, NormTrace, CandidateMatch,
ReviewTask), §2.23–2.30 (VendorFilterResult, VendorScoreCache, ScoreBreakdown,
StrategyRecommendation, SubstitutionRecommendation, ConsolidationInsight,
DataSourcesSnapshot, EvidenceRecord), §2.92–2.93 (JSON field policy notes),
CN-15, CN-16, CN-17, CN-19, CN-20.

Join Tables resolved per contract.md §2.93:
  • normalization_trace_merge(trace_id, bom_line_id)   — replaces UUID[] (CN-15)
  • consolidation_insight_line(insight_id, bom_line_id) — replaces UUID[] (CN-16)
  • data_sources_snapshot_link(snapshot_id, source_type, source_id) — (CN-17)

Invariants:
  • CN-19: auto-commit at confidence >= 0.85; route < 0.85 to review.
  • CN-20: vendor_score_cache rows are authoritative; bom_line.score_cache_json
           is a denormalized read cache only and never mutated independently.
  • Normalization_Trace is APPEND-ONLY.
  • vendor_score_cache TTL = 6h; invalidated by market data refresh or
    vendor capability / performance snapshot changes.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import (
    SignedMoney,
    DataSourceLinkType,
    DataFreshnessEnvelope,
    Confidence3,
    CountryCode,
    EvidenceDataPointType,
    FreshnessStatus,
    Money,
    NLPModelVersion,
    NormalizationDecisionType,
    NormalizationRunStatus,
    PGIBase,
    ProfileHash,
    ReviewTaskStatus,
    Score100,
    ScoreDimension,
    ScoringModelVersion,
    SourcingMode,
    VendorScoreConfidence,
)


# ──────────────────────────────────────────────────────────────────────────
# Normalization_Run (contract §2.9)
# ──────────────────────────────────────────────────────────────────────────

class NormalizationRunSchema(PGIBase):
    """A batch normalization run over a project's BOM lines.

    One run per project trigger (upload, typed intake, or admin replay).
    nlp_model_version is captured at run creation so diffs can be identified.
    """

    run_id: UUID
    project_id: UUID
    nlp_model_version: NLPModelVersion
    input_count: int = Field(ge=0)
    output_count: int = Field(ge=0)
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: NormalizationRunStatus


# ──────────────────────────────────────────────────────────────────────────
# Normalization_Trace (contract §2.10)
# ──────────────────────────────────────────────────────────────────────────

class NormalizationTraceSchema(PGIBase):
    """Immutable audit record of a single BOM line normalization decision.

    APPEND-ONLY — no updates, no deletes.
    raw_text: snapshot of BOM_Line.raw_text at the time of this trace.
    confidence: [0.0, 1.0]; auto-committed if >= 0.85 (CN-19).
    merged_with: computed from normalization_trace_merge join table (CN-15).
    """

    trace_id: UUID
    bom_line_id: UUID
    normalization_run_id: UUID
    raw_text: str = Field(description="Immutable snapshot of raw_text at trace time.")
    canonical_output_json: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence3
    decision_type: NormalizationDecisionType
    nlp_model_version: NLPModelVersion
    part_master_candidates_json: list[Any] = Field(default_factory=list)
    ambiguity_flags: list[Any] = Field(default_factory=list)
    split_from_bom_line_id: Optional[UUID] = None
    created_at: datetime
    part_id_match: Optional[UUID] = Field(
        default=None,
        description=(
            "Part_Master UUID matched by the NLP engine. Null when no match "
            "exceeded the similarity threshold."
        ),
    )

    # CN-15: merged_with_bom_line_ids is computed from join table, not stored
    merged_with: list[UUID] = Field(
        default_factory=list,
        description=(
            "BOM line IDs this line was merged into. Computed from "
            "normalization_trace_merge — never stored as UUID[] column."
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# normalization_trace_merge join table (CN-15)
# ──────────────────────────────────────────────────────────────────────────

class NormalizationTraceMergeSchema(PGIBase):
    """Join table entry linking a normalization trace to a merged BOM line.

    Replaces the UUID[] merged_with_bom_line_ids column on normalization_trace
    per contract CN-15.  Composite PK: (trace_id, bom_line_id).
    """

    trace_id: UUID
    bom_line_id: UUID


# ──────────────────────────────────────────────────────────────────────────
# Review_Task (contract §2.12)
# ──────────────────────────────────────────────────────────────────────────

class ReviewTaskSchema(PGIBase):
    """A normalization review task assigned to a buyer (SM-013).

    Created when BOM_Line confidence < 0.85 (CN-19).
    Resolved when buyer confirms or edits the canonical output.
    """

    task_id: UUID
    bom_line_id: UUID
    assigned_to: Optional[UUID] = None
    status: ReviewTaskStatus
    flags: list[Any] = Field(default_factory=list)
    created_at: datetime
    resolved_at: Optional[datetime] = None

    # Denormalized for convenience in review queue
    raw_text: Optional[str] = None
    normalized_name: Optional[str] = None
    normalization_confidence: Optional[Confidence3] = None


class ReviewTaskListResponse(PGIBase):
    """Cursor-paginated list of review tasks (attention queue)."""

    items: list[ReviewTaskSchema]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Score_Breakdown (contract §2.25)
# ──────────────────────────────────────────────────────────────────────────

class ScoreBreakdownSchema(PGIBase):
    """Per-dimension score breakdown row linked to a vendor_score_cache entry.

    breakdown_json on vendor_score_cache is authoritative; this table
    provides queryable per-dimension rows for analytics.
    """

    breakdown_id: UUID
    cache_id: UUID
    dimension: ScoreDimension
    score: Score100
    weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    weighted_contribution: Score100
    reasons_json: list[Any] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Score_Cache (contract §2.24)
# ──────────────────────────────────────────────────────────────────────────

class VendorScoreCacheEntry(PGIBase):
    """A cached vendor score for a specific (bom_line, vendor, weight_profile,
    market_context) combination (CN-20: authoritative source of ranking).

    Cache key: (bom_line_id, vendor_id, weight_profile_hash, market_context_hash).
    TTL: 6 hours (ttl_expires_at).
    Invalidated by: Vendor_Part_Capability update, Vendor_Performance_Snapshot
    rebuild, Baseline_Price / Forex_Rate / Tariff_Rate / Logistics_Rate refresh,
    or weight profile change on Project.
    """

    cache_id: UUID
    bom_line_id: UUID
    vendor_id: UUID
    total_score: Score100
    rank: int = Field(ge=1)
    weight_profile_hash: ProfileHash
    market_context_hash: ProfileHash
    breakdown_json: dict[str, Any] = Field(
        description=(
            "Authoritative per-dimension breakdown: "
            "{dimension: {score, weight, reasons, data_sources}}."
        )
    )
    explanation: str
    confidence: VendorScoreConfidence
    data_sources_snapshot_id: UUID
    scoring_model_version: ScoringModelVersion
    created_at: datetime
    ttl_expires_at: datetime

    # Denormalized for shortlist display
    vendor_name: Optional[str] = None
    vendor_country: Optional[CountryCode] = None
    vendor_tier: Optional[str] = None
    why_match: Optional[str] = None


class VendorShortlistResponse(PGIBase):
    """Response for GET /api/v1/bom-lines/{id}/shortlist.

    ranked_vendors: ordered by total_score DESC.
    eliminated_vendors: vendors removed at step 1 or 2 with reasons.
    strategy: recommended sourcing strategy for this line.
    risk_flags: risk signals from enrichment_json.
    """

    bom_line_id: UUID
    ranked_vendors: list[VendorScoreCacheEntry]
    eliminated_vendors: list["VendorFilterResultSchema"] = Field(default_factory=list)
    strategy: Optional["StrategyRecommendationSchema"] = None
    risk_flags: list[dict[str, Any]] = Field(default_factory=list)
    data_freshness: DataFreshnessEnvelope


# ──────────────────────────────────────────────────────────────────────────
# Strategy_Recommendation (contract §2.26)
# ──────────────────────────────────────────────────────────────────────────

class StrategyRecommendationSchema(PGIBase):
    """Recommended sourcing strategy for a BOM line.

    tlc_breakdown_json contains per-mode TLC computation results:
    {"modes": {"local_direct": {C_mfg, C_nre, C_log, T, C_fx, tlc_total}, ...}}.

    q_break: quantity at which the crossover between local_direct and
             international_direct is cost-neutral.
    """

    recommendation_id: UUID
    bom_line_id: UUID
    recommended_mode: SourcingMode
    tlc_breakdown_json: dict[str, Any] = Field(default_factory=dict)
    q_break: Optional[Decimal] = Field(
        default=None,
        description="Q_break crossover quantity (units). Null when not applicable.",
    )
    rationale: str
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Substitution_Recommendation (contract §2.27)
# ──────────────────────────────────────────────────────────────────────────

class SubstitutionRecommendationSchema(PGIBase):
    """A suggested alternative part for a BOM line.

    confidence [0.0, 1.0]: probability that the substitute is functionally
    equivalent given the spec_diff.
    """

    substitution_id: UUID
    bom_line_id: UUID
    suggested_part_id: UUID
    suggested_part_name: Optional[str] = None
    spec_diff_json: dict[str, Any] = Field(default_factory=dict)
    reason: str
    confidence: Confidence3
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Consolidation_Insight (contract §2.28)
# ──────────────────────────────────────────────────────────────────────────

class ConsolidationInsightSchema(PGIBase):
    """Insight recommending that multiple BOM lines be consolidated to one vendor.

    covered_bom_line_ids: computed from consolidation_insight_line join table (CN-16).
    """

    insight_id: UUID
    project_id: UUID
    vendor_id: UUID
    vendor_name: Optional[str] = None
    estimated_savings: SignedMoney
    rationale: str
    created_at: datetime

    # CN-16: computed from join table, never stored as UUID[] column
    covered_bom_line_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "BOM lines that can be consolidated to this vendor. "
            "Computed from consolidation_insight_line join table."
        ),
    )


class ConsolidationInsightLineSchema(PGIBase):
    """Join table entry: consolidation_insight_line(insight_id, bom_line_id) (CN-16)."""

    insight_id: UUID
    bom_line_id: UUID


class ConsolidationAnalysisResponse(PGIBase):
    """Response for POST /api/v1/projects/{id}/consolidation-analysis."""

    insights: list[ConsolidationInsightSchema]


# ──────────────────────────────────────────────────────────────────────────
# Data_Sources_Snapshot (contract §2.29)
# ──────────────────────────────────────────────────────────────────────────

class DataSourcesSnapshotSchema(PGIBase):
    """Audit snapshot of market data sources used in a scoring computation.

    source arrays (baseline_price_ids, forex_rate_ids, etc.) are computed
    from data_sources_snapshot_link join table (CN-17).
    fetched_at: timestamp when all sources were assembled — shown in UI
    alongside any data value (LAW-1, LAW-5).
    """

    snapshot_id: UUID
    scoring_cache_id: Optional[UUID] = None
    bom_line_id: UUID
    fetched_at: datetime

    # CN-17: all source arrays computed from join table
    baseline_price_ids: list[UUID] = Field(default_factory=list)
    forex_rate_ids: list[UUID] = Field(default_factory=list)
    tariff_rate_ids: list[UUID] = Field(default_factory=list)
    logistics_rate_ids: list[UUID] = Field(default_factory=list)
    vendor_performance_snapshot_ids: list[UUID] = Field(default_factory=list)


class DataSourcesSnapshotLinkSchema(PGIBase):
    """Join table entry: data_sources_snapshot_link(snapshot_id, source_type, source_id).

    Replaces the UUID[] array columns on data_sources_snapshot per CN-17.
    source_type: 'baseline_price' | 'forex_rate' | 'tariff_rate' |
                  'logistics_rate' | 'vendor_performance_snapshot'
    source_id:   UUID of the referenced row in the corresponding table.
    """

    snapshot_id: UUID
    source_type: DataSourceLinkType
    source_id: UUID


# ──────────────────────────────────────────────────────────────────────────
# Evidence_Record (contract §2.30)
# ──────────────────────────────────────────────────────────────────────────

class EvidenceRecordSchema(PGIBase):
    """A single market data point observed during intelligence processing.

    Provides fine-grained lineage for every number shown in the UI (LAW-2).
    freshness_status: FRESH | STALE | EXPIRED | LOCKED (SM-014).
    """

    evidence_id: UUID
    bom_line_id: UUID
    data_point_type: EvidenceDataPointType
    value: dict[str, Any] = Field(description="Structured data point value.")
    source: str = Field(max_length=128)
    provider: str = Field(max_length=128)
    fetched_at: datetime
    freshness_status: FreshnessStatus


# ──────────────────────────────────────────────────────────────────────────
# Forward reference: VendorFilterResultSchema (imported from vendor module)
# ──────────────────────────────────────────────────────────────────────────
from .vendor import VendorFilterResultSchema  # noqa: E402,F401

VendorShortlistResponse.model_rebuild()
