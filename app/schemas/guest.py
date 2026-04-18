"""
Pydantic schemas for the Guest Intelligence Report endpoint.

References: Blueprint Section 2.3
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuestIntelligenceRequest(BaseModel):
    """Request body for POST /guest/intelligence-report."""
    components: list[str] = Field(..., min_length=1, max_length=10, description="Raw text descriptions of components (1-10 items)")
    delivery_location: str = Field(..., description="City/country for delivery, free text")
    currency: str = Field(default="USD", description="ISO-4217 currency code")
    session_token: str | None = Field(default=None, description="Existing guest session token for continuity")


class FreshnessAnnotation(BaseModel):
    """Freshness status for a data point."""
    table: str = Field(description="Source table name")
    status: str = Field(description="FRESH | STALE | ESTIMATED | UNKNOWN")
    fetched_at: datetime | None = Field(default=None, description="When the data was last fetched")
    ttl_minutes: int = Field(default=0, description="Time-to-live in minutes")


class RiskFlag(BaseModel):
    """Risk flag for a component."""
    flag_type: str = Field(description="Type of risk: SINGLE_SOURCE | LONG_LEAD | HIGH_TARIFF | VOLATILE_PRICE")
    severity: str = Field(default="medium", description="low | medium | high")
    description: str = Field(default="", description="Human-readable risk description")


class PriceEstimate(BaseModel):
    """Price estimate with confidence and source."""
    unit_price: float = Field(description="Estimated unit price")
    currency: str = Field(default="USD")
    confidence: float = Field(default=0.7, description="0.0-1.0 confidence score")
    source: str = Field(default="historical", description="historical | live | benchmark")
    data_quality_label: str = Field(default="", description="FRESH | STALE | ESTIMATED | DEGRADED_ESTIMATE")


class EnrichedComponent(BaseModel):
    """Enriched component data returned to guest."""
    raw_text: str = Field(description="Original input text")
    canonical_name: str | None = Field(default=None, description="Normalized part name")
    category: str | None = Field(default=None, description="Product category")
    commodity_group: str | None = Field(default=None, description="Commodity group for scoring")
    confidence: float = Field(default=0.0, description="Normalization confidence 0.0-1.0")
    price_estimate: PriceEstimate | None = Field(default=None, description="Estimated pricing")
    risk_flags: list[RiskFlag] = Field(default_factory=list, description="Identified risks")


class RedactedVendor(BaseModel):
    """Vendor info with contact details redacted for guest users."""
    vendor_id: str = Field(description="Vendor identifier")
    display_name: str = Field(description="Vendor display name (may be partially masked)")
    country: str | None = Field(default=None)
    reliability_score: float = Field(default=0.0, description="0.0-1.0")
    total_score: float = Field(default=0.0, description="Overall match score")
    rank: int = Field(default=0, description="Rank in shortlist")
    score_breakdown: dict[str, Any] = Field(default_factory=dict, description="Score breakdown by dimension")
    data_quality_label: str = Field(default="", description="FRESH | DEGRADED_ESTIMATE")
    # Contact info explicitly NOT included


class StrategyOption(BaseModel):
    """Local vs international sourcing comparison."""
    mode: str = Field(description="local | international")
    estimated_tlc: float = Field(default=0.0, description="Total landed cost estimate")
    lead_time_days: int = Field(default=0, description="Estimated lead time")
    tariff_impact: float = Field(default=0.0, description="Tariff cost component")
    freight_estimate: float = Field(default=0.0, description="Freight cost component")
    currency: str = Field(default="USD")


class LockedFeatureTeaser(BaseModel):
    """Feature that requires sign-in to access."""
    feature: str = Field(description="Feature name")
    description: str = Field(description="What signing in unlocks")


class GuestIntelligenceResponse(BaseModel):
    """Response body for POST /guest/intelligence-report."""
    components: list[EnrichedComponent] = Field(default_factory=list, description="Enriched components with risk flags")
    vendor_shortlist: list[RedactedVendor] = Field(default_factory=list, description="Top 3 vendors (contact info redacted)")
    strategy_summary: list[StrategyOption] = Field(default_factory=list, description="Local vs international TLC comparison")
    freshness_report: list[FreshnessAnnotation] = Field(default_factory=list, description="Freshness status of underlying data")
    locked_features: list[LockedFeatureTeaser] = Field(default_factory=list, description="Features requiring sign-in")
    session_token: str = Field(description="Guest session token for continuity")

    model_config = ConfigDict(from_attributes=True)
