
"""
Recommendation response schemas for the runtime procurement pipeline.

These schemas represent the persisted procurement recommendation returned
after BOM normalization, enrichment, scoring, and recommendation generation.

Phase 2A Batch 4 scope:
- seeded vendor scoring remains authoritative
- Phase 2A evidence is additive when present
- evidence-weighted confidence and freshness-aware scoring
- recommendation strategy gating
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RecommendationFreshness(BaseModel):
    overall_status: str = "fresh"
    fx_status: str = "fresh"
    freight_status: str = "fresh"
    analyzer_status: str = "fresh"
    notes: list[str] = Field(default_factory=list)


class RecommendationPricingContext(BaseModel):
    source_currency: str = "USD"
    target_currency: str = "USD"
    fx_rate: float = 1.0
    fx_source: str = "seed_or_cached"
    fx_timestamp: str | None = None
    freight_mode: str = "baseline"
    freight_currency: str = "USD"
    freight_rate_per_kg: float | None = None
    freight_min_charge: float | None = None


class VendorRankingEntry(BaseModel):
    vendor_id: str
    vendor_name: str
    rank: int
    score: float
    confidence: str
    confidence_score: float | None = None
    raw_confidence_score: float | None = None
    calibrated_confidence_score: float | None = None
    rationale: str
    freshness_status: str
    source_currency: str = "USD"
    target_currency: str = "USD"
    fx_rate: float = 1.0
    estimated_unit_price: float | None = None
    estimated_line_total: float | None = None
    estimated_project_total: float | None = None
    estimated_freight_total: float | None = None
    average_lead_time_days: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class LineRecommendationEntry(BaseModel):
    bom_part_id: str
    row_number: int | None = None
    canonical_part_key: str | None = None
    normalized_text: str | None = None
    procurement_class: str | None = None
    quantity: float = 0.0
    unit: str | None = None
    confidence: float = 0.0
    recommended_vendor_id: str | None = None
    recommended_vendor_name: str | None = None
    rationale: str = ""
    freshness_status: str = "fresh"
    pricing_context: RecommendationPricingContext
    candidate_rankings: list[VendorRankingEntry] = Field(default_factory=list)
    strategy_gate: str = "verify-first"
    strategy_reasons: list[str] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    rank_changed: bool = False
    prior_rank: int | None = None
    score_delta: float | None = None
    material_change_flag: bool = False
    stability_reason: str = "not_evaluated"


class RecommendationSummary(BaseModel):
    recommended_vendor_id: str | None = None
    recommended_vendor_name: str | None = None
    total_lines: int = 0
    ranked_vendor_count: int = 0
    matched_vendor_count: int = 0
    target_currency: str = "USD"
    estimated_project_total: float | None = None
    cost_range_low: float | None = None
    cost_range_high: float | None = None
    estimated_lead_time_days: float | None = None
    confidence: str = "MEDIUM"
    rationale: str = ""
    strategy_gate: str = "verify-first"


class RecommendationEvidence(BaseModel):
    analyzer_runs: list[dict[str, Any]] = Field(default_factory=list)
    scoring_inputs: dict[str, Any] = Field(default_factory=dict)
    vendor_match_summary: dict[str, Any] = Field(default_factory=dict)
    fx_context: dict[str, Any] = Field(default_factory=dict)
    freight_context: dict[str, Any] = Field(default_factory=dict)
    phase2a_summary: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ProjectRecommendationResponse(BaseModel):
    project_id: str
    bom_id: str
    generated_at: datetime
    status: str = "success"
    summary: RecommendationSummary
    freshness: RecommendationFreshness
    vendor_rankings: list[VendorRankingEntry] = Field(default_factory=list)
    line_recommendations: list[LineRecommendationEntry] = Field(default_factory=list)
    evidence: RecommendationEvidence = Field(default_factory=RecommendationEvidence)

    model_config = {"from_attributes": True}

# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Vendor Intelligence Recommendation schemas
# Added per Execution Plan §6 (Recommendation Output) and §3 (Regional).
# All existing schemas above are preserved unchanged.
# ═════════════════════════════════════════════════════════════════════════════


class StrategyVendorOption(BaseModel):
    vendor_id: str
    vendor_name: str
    vendor_country: str | None = None
    vendor_state_region: str | None = None
    geo_tier: str = "unknown"  # local | regional | national | global
    rank_within_strategy: int = 0
    strategy_score: float = 0.0
    confidence: str = "LOW"  # HIGH | MEDIUM | LOW
    confidence_score: float = 0.0
    unit_price: str | None = None           # Decimals serialized as str
    freight_cost: str | None = None
    tariff_amount: str | None = None
    fx_conversion_cost: str | None = None
    landed_cost_total: str | None = None
    landed_cost_per_unit: str | None = None
    currency: str = "USD"
    lead_time_min_days: int | None = None
    lead_time_max_days: int | None = None
    lead_time_typical_days: float | None = None
    lead_time_reliability_score: float | None = None
    award_ready: bool = False
    rfq_recommended: bool = True
    rationale_narrative: str = ""
    trade_off_narrative: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class SourcingStrategyResult(BaseModel):
    strategy_name: str                   # fastest_local | best_domestic_value | lowest_landed_cost
    strategy_label: str
    top_option: StrategyVendorOption | None = None
    runner_up_options: list[StrategyVendorOption] = Field(default_factory=list)
    strategy_confidence: str = "LOW"
    rfq_required: bool = True
    strategy_narrative: str = ""
    geo_tier_context: dict[str, Any] = Field(default_factory=dict)
    commodity_signal: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class VendorIntelligenceRecommendationResponse(BaseModel):
    """Phase 3 recommendation output — multi-strategy, intelligence-rich."""
    project_id: str
    bom_id: str
    generated_at: datetime
    status: str = "success"
    requester_location: dict[str, Any] = Field(default_factory=dict)
    geo_context: dict[str, Any] = Field(default_factory=dict)
    sourcing_strategies: list[SourcingStrategyResult] = Field(default_factory=list)

    # Backward-compatibility with the existing ProjectRecommendationResponse:
    overall_summary: RecommendationSummary
    vendor_rankings: list[VendorRankingEntry] = Field(default_factory=list)
    line_recommendations: list[LineRecommendationEntry] = Field(default_factory=list)
    decision_safety_report: dict[str, Any] = Field(default_factory=dict)
    evidence: RecommendationEvidence = Field(default_factory=RecommendationEvidence)
    market_intelligence_context: dict[str, Any] = Field(default_factory=dict)
    freshness: RecommendationFreshness

    model_config = {"from_attributes": True}


class VendorIntelligenceProfile(BaseModel):
    """Full vendor intelligence profile returned from /vendors/{id}/intelligence."""
    vendor_id: str
    vendor_name: str
    trust_tier: str = "UNVERIFIED"
    trust_tier_details: dict[str, Any] = Field(default_factory=dict)
    profile_flags: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    export_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    lead_time_bands: list[dict[str, Any]] = Field(default_factory=list)
    communication_score: dict[str, Any] | None = None
    performance_snapshot: dict[str, Any] | None = None
    anomaly_history: list[dict[str, Any]] = Field(default_factory=list)
    primary_category_tag: str | None = None
    secondary_category_tags: list[str] = Field(default_factory=list)
    dedup_fingerprint: str | None = None
    last_validated_at: datetime | None = None

    model_config = {"from_attributes": True}
