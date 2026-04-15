
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