"""
b2b_contracts.py
PGI Hub Repo B inter-service contract schemas.

These Pydantic models define the request and response payloads for Repo C to
Repo B service calls: /normalize, /enrich, /score, /strategy, and /replay.
Named per contract section 8 as the single source for Repo B contracts.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    BaselinePriceSourceType,
    Carrier,
    Confidence3,
    CorrelationID,
    CountryCode,
    CurrencyCode,
    FreshnessStatus,
    HSCode,
    Money,
    NLPModelVersion,
    NormalizationDecisionType,
    PGIBase,
    Ratio,
    RiskFlag,
    RiskFlagDetail,
    Score100,
    ScoreDimension,
    ScoringModelVersion,
    Severity,
    SignedMoney,
    SourcingMode,
    VendorCapabilityConfidenceSource,
    VendorParticipation,
    VendorPlatformIntegrationLevel,
    VendorScoreConfidence,
    VendorTier,
    VendorType,
    WeightProfileValues,
)



# --- shared Repo B contract context ---

class RepoBDeliveryLocation(PGIBase):
    """Delivery location supplied to Repo B for logistics and scoring.

    Maps to Project.target_country / BOM_Line.target_country fields plus
    geocoordinates for logistics distance calculations.
    lat/lng are optional â€” Repo B falls back to country-level logistics data.
    """

    country: CountryCode
    state: Optional[str] = Field(default=None, max_length=128)
    city: Optional[str] = Field(default=None, max_length=255)
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Canonical BOM line output (produced by /normalize; consumed by /enrich,
# /score, /strategy as the "bom_line.canonical" nested object)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class CanonicalOutput(PGIBase):
    """Canonical structured representation of a normalized BOM line.

    Produced by Repo B /normalize (Â§5.2) and subsequently supplied back to
    Repo B in /enrich (Â§5.3), /score (Â§5.4), and /strategy (Â§5.5) calls.

    All fields except part_name, category, spec_json, quantity, and unit
    are optional â€” presence depends on the part category and normalization
    confidence.

    spec_json: category-specific structured specification (JSONB blob;
    justified per Â§2.93 â€” shape varies per part family).
    """

    part_name: str = Field(max_length=512)
    category: str = Field(max_length=128)
    spec_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Category-specific canonical spec. "
            "Structure varies per part family (justified JSONB, Â§2.93)."
        ),
    )
    quantity: Decimal = Field(gt=Decimal("0"))
    unit: str = Field(max_length=32)
    manufacturer_part_number: Optional[str] = Field(default=None, max_length=128)
    package_type: Optional[str] = Field(default=None, max_length=64)
    tolerance: Optional[str] = Field(default=None, max_length=64)
    voltage_rating: Optional[str] = Field(default=None, max_length=64)
    current_rating: Optional[str] = Field(default=None, max_length=64)
    material_grade: Optional[str] = Field(default=None, max_length=64)
    dimensions: Optional[str] = Field(default=None, max_length=128)
    finish: Optional[str] = Field(default=None, max_length=64)
    drawing_reference: Optional[str] = Field(default=None, max_length=128)
    required_certifications: list[str] = Field(default_factory=list)
    acceptable_substitutes: list[Any] = Field(default_factory=list)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Market context sub-models â€” one per data source type
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class BaselinePriceContext(PGIBase):
    """A baseline_price row serialized for Repo B consumption.

    freshness_status must be FRESH or STALE â€” Repo C must not send EXPIRED
    or LOCKED rows to Repo B for use in new computations.
    """

    price_id: UUID
    part_id: Optional[UUID] = None
    commodity_group: Optional[str] = Field(default=None, max_length=128)
    quantity_break: Decimal = Field(gt=Decimal("0"))
    price_floor: Money
    price_mid: Money
    price_ceiling: Money
    currency: CurrencyCode
    region: str = Field(max_length=64)
    source_type: BaselinePriceSourceType
    data_source_name: str = Field(max_length=128)
    fetched_at: datetime
    valid_until: datetime
    freshness_status: FreshnessStatus

    @model_validator(mode="after")
    def at_least_one_anchor(self) -> "BaselinePriceContext":
        if self.part_id is None and self.commodity_group is None:
            raise ValueError(
                "At least one of part_id or commodity_group must be non-null "
                "(baseline_price Â§2.31 constraint)."
            )
        return self


class TariffSnapshotContext(PGIBase):
    """A tariff_rate row serialized for Repo B consumption (Â§2.33)."""

    tariff_id: UUID
    hs_code: HSCode
    from_country: CountryCode
    to_country: CountryCode
    duty_rate: Decimal = Field(ge=Decimal("0"))
    vat_rate: Decimal = Field(ge=Decimal("0"))
    fta_eligible: bool = False
    fta_agreement_name: Optional[str] = Field(default=None, max_length=128)
    effective_date: date
    fetched_at: datetime
    freshness_status: FreshnessStatus


class LogisticsSnapshotContext(PGIBase):
    """A logistics_rate row serialized for Repo B consumption (Â§2.34).

    carrier: DHL | FedEx | UPS | Maersk | other  (CN-10/Â§3.59).
    """

    logistics_id: UUID
    origin_country: CountryCode
    destination_country: CountryCode
    carrier: Carrier
    service_level: str = Field(max_length=64)
    weight_band: str = Field(max_length=32)
    cost_estimate: Money
    currency: CurrencyCode
    transit_days_min: int = Field(ge=0)
    transit_days_max: int = Field(ge=0)
    fetched_at: datetime
    valid_until: datetime
    freshness_status: FreshnessStatus

    @model_validator(mode="after")
    def max_gte_min(self) -> "LogisticsSnapshotContext":
        if self.transit_days_max < self.transit_days_min:
            raise ValueError(
                "transit_days_max must be >= transit_days_min (Â§2.34 CHECK constraint)."
            )
        return self


class ForexSnapshotContext(PGIBase):
    """A forex_rate row serialized for Repo B consumption (Â§2.32).

    LOCKED rows (locked to a quote or PO) are acceptable here â€” Repo C may
    supply them when computing TLC on an already-accepted quote.
    """

    rate_id: UUID
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Decimal = Field(gt=Decimal("0"))
    fetched_at: datetime
    valid_until: datetime
    freshness_status: FreshnessStatus


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Assembled market context (used in /enrich, /score, /strategy)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class MarketContextPayload(PGIBase):
    """Pre-assembled market context supplied to Repo B on every intelligence call.

    Repo B NEVER fetches external data â€” all market context is assembled by
    Repo C (Â§1.2, Â§1.3, Â§5.1) with freshness validation before the call.

    baseline_prices: relevant baseline prices for the BOM line's part or
                     commodity group.
    tariff_snapshot: applicable tariff rate(s) for the delivery route.
    logistics_snapshot: applicable logistics rate(s) for the delivery route.
    forex_snapshot: applicable forex rate(s) for the delivery currency pair(s).
    """

    baseline_prices: list[BaselinePriceContext] = Field(default_factory=list)
    tariff_snapshot: list[TariffSnapshotContext] = Field(default_factory=list)
    logistics_snapshot: list[LogisticsSnapshotContext] = Field(default_factory=list)
    forex_snapshot: list[ForexSnapshotContext] = Field(default_factory=list)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Vendor sub-models for /score and /strategy
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class VendorCandidateProfile(PGIBase):
    """Subset of Vendor fields relevant for scoring (Â§2.13).

    participation: SM-009 â€” only BASIC, STANDARD, PREMIUM vendors pass
    hard filter at the Repo C candidate-selection step before being sent
    here.  SUSPENDED/DEACTIVATED vendors are filtered out by Repo C.
    """

    vendor_id: UUID
    name: str = Field(max_length=255)
    country_of_origin: CountryCode
    vendor_type: VendorType
    participation: VendorParticipation
    tier: VendorTier
    verified_badge: bool = False
    substitute_willingness: bool = False
    engineering_support: bool = False
    platform_integration_level: VendorPlatformIntegrationLevel
    currency_support: list[CurrencyCode] = Field(default_factory=list)
    ships_on_their_own: bool = False


class VendorCandidateCapability(PGIBase):
    """Vendor_Part_Capability fields relevant for scoring (Â§2.15).

    confidence_source: historical | declared | inferred (Â§3.41).
    """

    capability_id: Optional[UUID] = None
    vendor_id: UUID
    commodity_group: str = Field(max_length=128)
    category: Optional[str] = Field(default=None, max_length=128)
    moq: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    lead_time_weeks_min: Optional[Decimal] = None
    lead_time_weeks_max: Optional[Decimal] = None
    unit_cost_floor: Optional[Money] = None
    unit_cost_ceiling: Optional[Money] = None
    currency: Optional[CurrencyCode] = None
    confidence_source: VendorCapabilityConfidenceSource = VendorCapabilityConfidenceSource.INFERRED
    last_validated_at: Optional[datetime] = None


class VendorPerformanceSnapshot(PGIBase):
    """Vendor_Performance_Snapshot fields relevant for scoring (Â§2.16).

    All score fields are 0â€“100 Decimal with 3 decimal places.
    Built nightly by Repo C (Â§8 vendor_snapshot_worker.py).
    """

    snapshot_id: Optional[UUID] = None
    vendor_id: UUID
    snapshot_date: Optional[date] = None
    on_time_delivery_rate: Optional[Score100] = None
    defect_rate_pct: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("100")
    )
    response_time_hours_avg: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    quote_acceptance_rate: Optional[Score100] = None
    dispute_rate_pct: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("100")
    )
    total_orders: int = Field(default=0, ge=0)
    composite_score: Optional[Score100] = None


class VendorCandidatePayload(PGIBase):
    """Full vendor candidate bundle supplied to Repo B /score (Â§5.4).

    Composed by Repo C's intelligence orchestrator from:
    - vendor (Â§2.13)
    - vendor_part_capability (Â§2.15)
    - vendor_performance_snapshot (Â§2.16)
    """

    vendor_id: UUID
    profile: VendorCandidateProfile
    capability: Optional[VendorCandidateCapability] = None
    performance_snapshot: Optional[VendorPerformanceSnapshot] = None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Strategy-specific vendor input (Â§5.5 â€” reduced from VendorCandidatePayload)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class CostBand(PGIBase):
    """A {floor, mid, ceiling} monetary band in a given currency."""

    floor: Money
    mid: Money
    ceiling: Money
    currency: CurrencyCode


class StrategyVendorInput(PGIBase):
    """Reduced vendor record supplied to Repo B /strategy (Â§5.5).

    Only the fields needed for TLC computation and sourcing mode comparison
    are included â€” Repo C filters the full vendor record before building
    this payload.
    """

    vendor_id: UUID
    country_of_origin: CountryCode
    unit_cost_band: CostBand


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Part master hint (used in /normalize requests â€” Â§5.2)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PartMasterHint(PGIBase):
    """A Part_Master candidate hint supplied to Repo B for /normalize.

    Repo C pre-fetches nearest-neighbor Part_Master candidates using
    pgvector similarity search and provides them to Repo B to constrain
    the NLP normalization search space.

    similarity: cosine similarity score from the pgvector IVFFlat/HNSW index
    (0.0 = unrelated, 1.0 = identical).
    """

    part_id: UUID
    canonical_name: str = Field(max_length=512)
    spec_template: dict[str, Any] = Field(
        default_factory=dict,
        description="Part_Master.spec_template for the candidate part.",
    )
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="pgvector cosine similarity score.",
    )

# --- normalize endpoint contract ---

class NormalizeRowInput(PGIBase):
    """A single raw BOM line submitted for normalization.

    raw_id: the BOM_Line.bom_line_id UUID â€” echoed back in the response so
    Repo C can correlate results to persisted rows without relying on order.
    raw_text: the IMMUTABLE BOM_Line.raw_text snapshot (Â§2.7 invariant).
    qty: the BOM_Line.quantity at time of normalization request.
    unit: the BOM_Line.unit (may be null for unstructured single-search input).
    """

    raw_id: UUID = Field(
        description="BOM_Line.bom_line_id â€” used to correlate response results.",
    )
    raw_text: str = Field(
        min_length=1,
        description=(
            "Immutable raw text snapshot of the BOM line. "
            "Must match BOM_Line.raw_text exactly."
        ),
    )
    qty: Decimal = Field(
        gt=Decimal("0"),
        description="Requested quantity (BOM_Line.quantity).",
    )
    unit: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Unit of measure (BOM_Line.unit); may be null for unstructured input.",
    )


class NormalizeRequest(PGIBase):
    """Full request body for POST /api/v1/normalize (Â§5.2).

    rows: 1â€“200 BOM lines per batch.  Repo C controls batch size to respect
    Repo B memory limits and the 30 s timeout.
    part_master_hints: pgvector nearest-neighbour candidates; empty list if
    no candidates found above the similarity threshold.
    nlp_model_version: must match the active NLP model on Repo B; a mismatch
    returns HTTP 422 nlp_model_version_mismatch, triggering a fallback on
    Repo C.
    """

    rows: list[NormalizeRowInput] = Field(
        min_length=1,
        max_length=200,
        description="Batch of raw BOM lines to normalize (1â€“200 rows).",
    )
    part_master_hints: list[PartMasterHint] = Field(
        default_factory=list,
        description=(
            "Part_Master candidates from pgvector search. "
            "Sorted by similarity DESC; Repo B uses these to constrain "
            "the embedding search space."
        ),
    )
    nlp_model_version: NLPModelVersion = Field(
        description="Expected active NLP model version on Repo B.",
    )
    correlation_id: CorrelationID = Field(
        description="W3C-compatible correlation ID propagated from the originating request.",
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response sub-models
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class NormalizeCanonicalOutput(CanonicalOutput):
    """Canonical output produced by Repo B for a single BOM line.

    Extends CanonicalOutput with fields that are only present in the
    normalize response (not the downstream enrich/score/strategy payloads).

    All fields inherited from CanonicalOutput are populated by Repo B;
    Repo C writes them to BOM_Line columns and Normalization_Trace.canonical_output_json.
    """

    # No additional fields beyond CanonicalOutput â€” subclassed to allow
    # future extension without breaking the common contract.
    pass


class NormalizePartMasterCandidate(PGIBase):
    """A Part_Master candidate identified by Repo B during normalization.

    Returned in part_master_candidates to let Repo C update candidate_match
    rows (Â§2.11) and link BOM_Line.part_id when confidence is high enough.

    similarity: cosine similarity between the raw embedding and the candidate
    part embedding (0.0â€“1.0).
    """

    part_id: UUID
    similarity: float = Field(ge=0.0, le=1.0)


class NormalizeResult(PGIBase):
    """Normalization result for a single input row (Â§5.2 response.results[]).

    raw_id: matches NormalizeRowInput.raw_id for correlation.
    canonical: the structured canonical output from the NLP pipeline.
    part_id_match: the top Part_Master match UUID if confidence >= threshold;
                   null when no confident match found.
    part_master_candidates: ranked candidate list for Repo C to persist as
                            Candidate_Match rows (Â§2.11).
    confidence: [0.0, 1.0].  Auto-commit when >= 0.85 (CN-19).
    decision: 'auto' (>= 0.85) | 'review' (< 0.85) | 'manual' (no output).
    ambiguity_flags: list of free-form strings describing ambiguities Repo B
                     detected (e.g. "multiple_part_families", "unit_ambiguous").
    split_from: raw_id of the parent line if this result was split by Repo B
                from a multi-component raw line; null for primary lines.
    merged_with: raw_ids of lines that were merged into this result by
                 Repo B.  Repo C creates normalization_trace_merge rows
                 (CN-15) â€” NOT stored as UUID[] on normalization_trace.
    """

    raw_id: UUID = Field(
        description="Echoed BOM_Line.bom_line_id for correlation.",
    )
    canonical: NormalizeCanonicalOutput = Field(
        description="Structured canonical output from the NLP normalization pipeline.",
    )
    part_id_match: Optional[UUID] = Field(
        default=None,
        description=(
            "Top Part_Master match UUID. "
            "Non-null when confidence >= auto-commit threshold and a match exists."
        ),
    )
    part_master_candidates: list[NormalizePartMasterCandidate] = Field(
        default_factory=list,
        description=(
            "Ranked Part_Master candidates for Repo C to persist as "
            "candidate_match rows (Â§2.11)."
        ),
    )
    confidence: Confidence3 = Field(
        description=(
            "Normalization confidence [0.000, 1.000]. "
            "CN-19: auto-commit at >= 0.85; route to review when < 0.85."
        ),
    )
    decision: NormalizationDecisionType = Field(
        description=(
            "Normalization decision type. "
            "Repo B sets 'auto' when confidence >= 0.85 and canonical is complete, "
            "'review' when < 0.85, 'manual' when pipeline cannot produce output."
        ),
    )
    ambiguity_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable flags describing detected ambiguities "
            "(e.g. 'multiple_part_families', 'quantity_unit_mismatch')."
        ),
    )
    split_from: Optional[UUID] = Field(
        default=None,
        description=(
            "BOM_Line.bom_line_id of the parent line if this result was split "
            "from a multi-component raw line. Null for primary lines."
        ),
    )
    merged_with: list[UUID] = Field(
        default_factory=list,
        description=(
            "BOM_Line.bom_line_ids of lines merged into this result. "
            "Repo C writes normalization_trace_merge rows for each entry "
            "(CN-15 â€” not a UUID[] column on normalization_trace)."
        ),
    )

    @model_validator(mode="after")
    def decision_consistent_with_confidence(self) -> "NormalizeResult":
        """Validate that decision aligns with confidence per CN-19."""
        if (
            self.decision == NormalizationDecisionType.AUTO
            and self.confidence < Decimal("0.85")
        ):
            raise ValueError(
                "decision='auto' requires confidence >= 0.85 (CN-19). "
                f"Got confidence={self.confidence}."
            )
        if (
            self.decision == NormalizationDecisionType.REVIEW_APPROVED
            and self.confidence >= Decimal("0.85")
        ):
            # review_approved can appear in replay results â€” allow it but
            # flag as a data quality note; do not reject.
            pass
        return self


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class NormalizeResponse(PGIBase):
    """Full response body for POST /api/v1/normalize (Â§5.2).

    nlp_model_version: the active NLP model version on Repo B at call time.
    Repo C compares this against the requested version to detect drift.

    results: one entry per input row in NormalizeRequest.rows.
    The order of results is NOT guaranteed to match the order of rows â€”
    Repo C must correlate by raw_id.
    """

    nlp_model_version: NLPModelVersion = Field(
        description=(
            "Active NLP model version stamp from Repo B. "
            "Repo C records this on Normalization_Run.nlp_model_version."
        ),
    )
    results: list[NormalizeResult] = Field(
        description=(
            "One result per input row. "
            "Correlation is by NormalizeResult.raw_id â€” order is not guaranteed."
        ),
    )

    @model_validator(mode="after")
    def results_non_empty(self) -> "NormalizeResponse":
        if not self.results:
            raise ValueError(
                "NormalizeResponse.results must contain at least one entry."
            )
        return self

# --- enrich endpoint contract ---

class EnrichBOMLineInput(PGIBase):
    """The BOM line reference and canonical data supplied to /enrich.

    bom_line_id: Repo C's persisted BOM_Line.bom_line_id â€” Repo B echoes
    this in any error payloads for correlation.
    canonical: the canonical output produced by /normalize and persisted
    on BOM_Line (normalized_name, category, spec_json, etc.).
    """

    bom_line_id: UUID = Field(
        description="BOM_Line.bom_line_id for error correlation.",
    )
    canonical: CanonicalOutput = Field(
        description=(
            "Canonical structured representation of the BOM line "
            "as produced by the /normalize pipeline."
        ),
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Request
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EnrichRequest(PGIBase):
    """Full request body for POST /api/v1/enrich (Â§5.3).

    Repo C assembles this after:
    1. Verifying BOM_Line.status == NORMALIZED.
    2. Querying baseline_price, tariff_rate, logistics_rate, forex_rate
       with freshness validation (LAW-5).
    3. Transitioning BOM_Line.status â†’ ENRICHING.
    """

    bom_line: EnrichBOMLineInput = Field(
        description="BOM line reference and canonical data.",
    )
    delivery_location: RepoBDeliveryLocation = Field(
        description=(
            "Delivery destination used for logistics costing and tariff "
            "route selection."
        ),
    )
    market_context: MarketContextPayload = Field(
        description=(
            "Pre-assembled and freshness-validated market context. "
            "Repo B NEVER fetches external data â€” all context is supplied here."
        ),
    )
    correlation_id: CorrelationID = Field(
        description="W3C-compatible correlation ID propagated from the originating request.",
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response sub-models
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EnrichPriceBandResult(PGIBase):
    """Price band computed by Repo B from the supplied baseline_prices.

    floor / mid / ceiling: market price bounds for this BOM line.
    source: human-readable label of the primary data source used.
    fetched_at: timestamp of the most recently fetched baseline_price row
    that contributed to this band.
    """

    floor: Money = Field(description="Lower bound of the market price band.")
    mid: Money = Field(description="Mid-point (median) of the market price band.")
    ceiling: Money = Field(description="Upper bound of the market price band.")
    currency: CurrencyCode
    source: str = Field(
        max_length=255,
        description=(
            "Data source label (e.g. 'Digi-Key + Mouser (avg)', "
            "'IHS Markit commodity band')."
        ),
    )
    fetched_at: datetime = Field(
        description="Timestamp of the most recent baseline_price row used.",
    )

    @model_validator(mode="after")
    def band_is_ordered(self) -> "EnrichPriceBandResult":
        if self.floor > self.mid:
            raise ValueError("price_band.floor must be <= price_band.mid.")
        if self.mid > self.ceiling:
            raise ValueError("price_band.mid must be <= price_band.ceiling.")
        return self


class EnrichTariffResult(PGIBase):
    """Tariff summary computed by Repo B from the supplied tariff_snapshot.

    snapshot_id: the tariff_rate.tariff_id of the primary row used.
    Repo C uses this to link the enrichment to the source tariff_rate row
    and set Quote.tariff_snapshot_id at submission.
    """

    duty_rate: Decimal = Field(
        ge=Decimal("0"),
        description="Applied duty rate as a decimal (e.g. 0.05 = 5%).",
    )
    vat_rate: Decimal = Field(
        ge=Decimal("0"),
        description="Applied VAT/GST rate as a decimal.",
    )
    fta_eligible: bool = Field(
        description="Whether an FTA waiver is applicable for this route.",
    )
    snapshot_id: UUID = Field(
        description=(
            "tariff_rate.tariff_id of the primary tariff row used. "
            "Stored as Quote.tariff_snapshot_id at quote submission."
        ),
    )


class EnrichLogisticsCostBand(PGIBase):
    """A {floor, mid, ceiling} freight cost estimate band."""

    floor: Money
    mid: Money
    ceiling: Money

    @model_validator(mode="after")
    def band_is_ordered(self) -> "EnrichLogisticsCostBand":
        if self.floor > self.mid:
            raise ValueError("logistics.cost_band.floor must be <= mid.")
        if self.mid > self.ceiling:
            raise ValueError("logistics.cost_band.mid must be <= ceiling.")
        return self


class EnrichLogisticsResult(PGIBase):
    """Logistics cost and transit time summary computed by Repo B.

    cost_band: freight cost estimate in the delivery currency.
    transit_days_min / transit_days_max: estimated transit time range
    computed from the most relevant logistics_rate row(s).
    """

    cost_band: EnrichLogisticsCostBand
    transit_days_min: int = Field(ge=0)
    transit_days_max: int = Field(ge=0)

    @model_validator(mode="after")
    def max_gte_min(self) -> "EnrichLogisticsResult":
        if self.transit_days_max < self.transit_days_min:
            raise ValueError(
                "logistics.transit_days_max must be >= transit_days_min."
            )
        return self


class EnrichmentOutput(PGIBase):
    """The composite enrichment_json structure produced by Repo B (Â§5.3).

    This maps directly to BOM_Line.enrichment_json (justified JSONB per Â§2.93:
    composite of four domains with variable shape).

    risk_flags: uses RiskFlagDetail from common.py which enforces the
    contract vocabulary: flag âˆˆ Â§3.76, severity âˆˆ Â§3.78.
    """

    price_band: EnrichPriceBandResult = Field(
        description="Market price band for this BOM line.",
    )
    tariff: EnrichTariffResult = Field(
        description="Tariff duty and VAT summary for the delivery route.",
    )
    logistics: EnrichLogisticsResult = Field(
        description="Freight cost band and transit time estimate.",
    )
    risk_flags: list[RiskFlagDetail] = Field(
        default_factory=list,
        description=(
            "Risk flags detected by Repo B. "
            "flag âˆˆ {SOLE_SOURCE, LONG_LEAD, HIGH_TARIFF_EXPOSURE, "
            "CURRENCY_VOLATILE, GEOPOLITICAL_RISK, COMPLIANCE_GAP} (Â§3.76). "
            "severity âˆˆ {LOW, MEDIUM, HIGH, CRITICAL} (Â§3.78)."
        ),
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EnrichResponse(PGIBase):
    """Full response body for POST /api/v1/enrich (Â§5.3).

    enrichment_json: the composite enrichment output that Repo C writes
    directly to BOM_Line.enrichment_json.

    Repo C also:
    1. Transitions BOM_Line.status: ENRICHING â†’ ENRICHED.
    2. Creates Evidence_Record rows for each contributing data point (Â§2.30).
    3. Creates a Data_Sources_Snapshot row (Â§2.29) linking to source IDs
       via data_sources_snapshot_link (CN-17).
    4. Writes a Data_Freshness_Log entry (Â§2.75).
    """

    enrichment_json: EnrichmentOutput = Field(
        description=(
            "Composite enrichment output. "
            "Written to BOM_Line.enrichment_json by Repo C after response."
        ),
    )

# --- score endpoint contract ---

class ScoreBOMLineInput(PGIBase):
    """The BOM line bundle supplied to /score.

    enrichment_json is included so Repo B can incorporate enrichment data
    (price band, tariff, logistics, risk flags) directly into scoring without
    a second Repo C â†’ Repo B roundtrip.
    """

    bom_line_id: UUID = Field(
        description="BOM_Line.bom_line_id for error correlation.",
    )
    canonical: CanonicalOutput = Field(
        description="Canonical normalized form of the BOM line.",
    )
    enrichment_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "BOM_Line.enrichment_json as written by the /enrich pipeline. "
            "Contains price_band, tariff, logistics, and risk_flags composites "
            "(Â§2.93 justified JSONB)."
        ),
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Request
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ScoreRequest(PGIBase):
    """Full request body for POST /api/v1/score (Â§5.4).

    Repo C builds this after:
    1. Running hard-filter (participation, geography, certification) on all
       marketplace vendors â€” eliminated vendors written to Vendor_Filter_Result.
    2. Collecting BOM_Line.canonical + enrichment_json.
    3. Fetching the active weight_profile for the project.
    4. Assembling market_context with freshness validation.
    5. Transitioning BOM_Line.status â†’ SCORING.
    """

    bom_line: ScoreBOMLineInput = Field(
        description="BOM line canonical form and enrichment data for scoring.",
    )
    vendor_candidates: list[VendorCandidatePayload] = Field(
        min_length=1,
        description=(
            "Vendor candidates that passed Repo C hard-filter. "
            "Each includes profile, capability, and performance_snapshot."
        ),
    )
    weight_profile: WeightProfileValues = Field(
        description=(
            "5-dimension weight vector â€” must sum to 1.0 Â±0.001. "
            "Derived from Project.weight_profile / weight_profile_custom_json."
        ),
    )
    delivery_location: RepoBDeliveryLocation = Field(
        description="Delivery destination for logistics dimension scoring.",
    )
    market_context: MarketContextPayload = Field(
        description="Pre-assembled and freshness-validated market context.",
    )
    scoring_model_version: ScoringModelVersion = Field(
        description="Expected active scoring model version on Repo B.",
    )
    correlation_id: CorrelationID = Field(
        description="W3C-compatible correlation ID.",
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response sub-models
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ScoreDimensionBreakdown(PGIBase):
    """Per-dimension scoring detail for a single vendor.

    Maps to Score_Breakdown (Â§2.25) and breakdown_json in Vendor_Score_Cache
    (Â§2.24).

    score: 0â€“100 per-dimension raw score.
    weight: the weight applied to this dimension from WeightProfileValues.
    weighted_contribution: score Ã— weight (used to compute total_score).
    reasons: human-readable explanation snippets for LAW-2 (explain everything).
    data_sources: references to the market data rows that drove this dimension.
    """

    score: Score100 = Field(
        description="Raw dimension score (0â€“100, 3 decimal places).",
    )
    weight: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Weight applied to this dimension from the weight profile.",
    )
    weighted_contribution: Decimal = Field(
        ge=Decimal("0"),
        description=(
            "score Ã— weight â€” contribution to the vendor's total_score. "
            "Stored in Score_Breakdown.weighted_contribution (Â§2.25)."
        ),
    )
    reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Plain-language reasons for this dimension score (LAW-2). "
            "Surfaced in the vendor scorecard explanation."
        ),
    )
    data_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Data source identifiers that contributed to this dimension "
            "(e.g. 'baseline_price:uuid', 'vendor_performance_snapshot:uuid'). "
            "Stored in Score_Breakdown.reasons_json (Â§2.25)."
        ),
    )


class ScoreBreakdownMap(PGIBase):
    """The full 5-dimension breakdown for a single ranked vendor.

    Maps to Vendor_Score_Cache.breakdown_json (Â§2.24) â€” the authoritative
    per-vendor score decomposition (CN-20).

    All five dimensions are always present; weights sum to 1.0 (enforced
    at the WeightProfileValues level in the request).
    """

    cost_competitiveness: ScoreDimensionBreakdown
    lead_time_availability: ScoreDimensionBreakdown
    quality_reliability: ScoreDimensionBreakdown
    strategic_fit: ScoreDimensionBreakdown
    operational_capability: ScoreDimensionBreakdown

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ScoreBreakdownMap":
        total = (
            self.cost_competitiveness.weight
            + self.lead_time_availability.weight
            + self.quality_reliability.weight
            + self.strategic_fit.weight
            + self.operational_capability.weight
        )
        if abs(float(total) - 1.0) > 0.001:
            raise ValueError(
                f"Dimension weights in score breakdown must sum to 1.0; "
                f"got {total:.4f}."
            )
        return self


class RankedVendorResult(PGIBase):
    """Scoring result for a single vendor candidate (Â§5.4 response).

    Maps to a Vendor_Score_Cache row (Â§2.24) written by Repo C after
    receiving this response.

    vendor_id: echoed from ScoreRequest.vendor_candidates[].vendor_id.
    total_score: weighted sum across all 5 dimensions (0â€“100).
    rank: 1-indexed rank within this scoring run (lowest rank = best).
    breakdown: 5-dimension detail (stored in breakdown_json, Â§2.24).
    explanation: Jinja2-rendered plain-language explanation (LAW-2).
                 Repo B produces this from its explanation template engine.
    confidence: HIGH | MEDIUM | LOW based on data completeness (Â§3.42).
    """

    vendor_id: UUID = Field(
        description="Echoed from ScoreRequest.vendor_candidates[].vendor_id.",
    )
    total_score: Score100 = Field(
        description=(
            "Total weighted score (0â€“100, 3 decimal places). "
            "Stored in Vendor_Score_Cache.total_score (Â§2.24)."
        ),
    )
    rank: int = Field(
        ge=1,
        description=(
            "1-indexed rank within this scoring run (1 = best). "
            "Stored in Vendor_Score_Cache.rank (Â§2.24)."
        ),
    )
    breakdown: ScoreBreakdownMap = Field(
        description=(
            "5-dimension breakdown. Stored in Vendor_Score_Cache.breakdown_json "
            "and in Score_Breakdown rows (Â§2.24, Â§2.25, CN-20)."
        ),
    )
    explanation: str = Field(
        description=(
            "Plain-language explanation of the score (LAW-2 â€” explain everything). "
            "Produced by Repo B's Jinja2 explanation template engine. "
            "Stored in Vendor_Score_Cache.explanation (Â§2.24)."
        ),
    )
    confidence: VendorScoreConfidence = Field(
        description=(
            "Scoring confidence: HIGH | MEDIUM | LOW. "
            "Reflects data completeness (performance snapshot coverage, "
            "capability confidence source, freshness of market data). "
            "Stored in Vendor_Score_Cache.confidence (Â§2.24, Â§3.42)."
        ),
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ScoreResponse(PGIBase):
    """Full response body for POST /api/v1/score (Â§5.4).

    scoring_model_version: stamped by Repo B at call time (Â§1.2 Repo B
    exclusive).  Repo C stores this on Vendor_Score_Cache.scoring_model_version.

    ranked_vendors: sorted by rank ASC (rank=1 is the top-scoring vendor).
    One entry per vendor_candidate in the request.  The list length must
    match len(ScoreRequest.vendor_candidates).

    Repo C post-processing:
    1. Writes one Vendor_Score_Cache row per ranked_vendor (authoritative, CN-20).
    2. Writes Score_Breakdown rows for each dimension (Â§2.25).
    3. Creates a Data_Sources_Snapshot (Â§2.29) via data_sources_snapshot_link.
    4. Updates BOM_Line.score_cache_json as a denormalized read cache (CN-20).
    5. Transitions BOM_Line.status: SCORING â†’ SCORED.
    6. Sets ttl_expires_at = now() + 6 h on each Vendor_Score_Cache row.
    """

    scoring_model_version: ScoringModelVersion = Field(
        description=(
            "Active scoring model version stamp from Repo B. "
            "Stored in Vendor_Score_Cache.scoring_model_version (Â§2.24)."
        ),
    )
    ranked_vendors: list[RankedVendorResult] = Field(
        min_length=1,
        description=(
            "Scored and ranked vendor list, sorted by rank ASC. "
            "One entry per vendor_candidate in the request."
        ),
    )

    @model_validator(mode="after")
    def ranks_are_sequential(self) -> "ScoreResponse":
        """Validate rank sequence: must be 1, 2, 3, â€¦ without gaps or duplicates."""
        ranks = sorted(v.rank for v in self.ranked_vendors)
        expected = list(range(1, len(ranks) + 1))
        if ranks != expected:
            raise ValueError(
                f"ranked_vendors ranks must be sequential starting at 1. "
                f"Got: {ranks}."
            )
        return self

# --- strategy endpoint contract ---

class StrategyBOMLineInput(PGIBase):
    """BOM line bundle supplied to /strategy.

    Includes enrichment_json so Repo B can access the price_band, tariff,
    and logistics data it needs for TLC formula inputs without a separate call.
    """

    bom_line_id: UUID = Field(
        description="BOM_Line.bom_line_id for error correlation.",
    )
    canonical: CanonicalOutput = Field(
        description="Canonical normalized form of the BOM line.",
    )
    enrichment_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "BOM_Line.enrichment_json from the /enrich pipeline. "
            "Contains price_band, tariff, logistics, and risk_flags."
        ),
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Request
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class StrategyRequest(PGIBase):
    """Full request body for POST /api/v1/strategy (Â§5.5).

    Repo C builds this after BOM_Line.status has reached SCORED, using
    the top-scoring vendor candidates for TLC evaluation.

    candidate_vendors: reduced vendor records (StrategyVendorInput)
    containing only the fields needed for TLC math â€” vendor_id,
    country_of_origin, and unit_cost_band.  Full vendor profiles are not
    repeated here since scoring is already complete.

    quantity: the BOM line quantity used for Q_break crossover analysis.
    """

    bom_line: StrategyBOMLineInput = Field(
        description="BOM line canonical form and enrichment data.",
    )
    candidate_vendors: list[StrategyVendorInput] = Field(
        min_length=1,
        description=(
            "Reduced vendor records for TLC calculation. "
            "Each provides vendor_id, country_of_origin, and unit_cost_band."
        ),
    )
    market_context: MarketContextPayload = Field(
        description="Pre-assembled market context (tariff, logistics, forex).",
    )
    delivery_location: RepoBDeliveryLocation = Field(
        description="Delivery destination for logistics TLC component.",
    )
    quantity: Decimal = Field(
        gt=Decimal("0"),
        description="BOM_Line.quantity â€” used for Q_break crossover analysis.",
    )
    correlation_id: CorrelationID = Field(
        description="W3C-compatible correlation ID.",
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response sub-models
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TLCModeBreakdown(PGIBase):
    """Total Landed Cost breakdown for a single sourcing mode.

    TLC formula components (Â§5.5):
      C_mfg     â€” manufacturing / unit material cost
      C_nre     â€” non-recurring engineering cost (amortized)
      C_log     â€” logistics / freight cost
      T         â€” tariff / duty cost
      C_fx      â€” forex risk / conversion cost
      tlc_total â€” sum of all components

    All monetary values are in the delivery currency.
    """

    C_mfg: Money = Field(
        description="Manufacturing / unit material cost component.",
    )
    C_nre: Money = Field(
        description="Non-recurring engineering cost (amortized per unit).",
    )
    C_log: Money = Field(
        description="Logistics / freight cost component.",
    )
    T: Money = Field(
        description="Tariff / duty cost component.",
    )
    C_fx: SignedMoney = Field(
        description="Forex risk / conversion cost component.",
    )
    tlc_total: SignedMoney = Field(
        description="Total Landed Cost = C_mfg + C_nre + C_log + T + C_fx.",
    )

    @model_validator(mode="after")
    def tlc_total_matches_components(self) -> "TLCModeBreakdown":
        """Validate tlc_total = sum of components (within Decimal precision)."""
        computed = self.C_mfg + self.C_nre + self.C_log + self.T + self.C_fx
        delta = abs(computed - self.tlc_total)
        # Allow up to 0.01 rounding tolerance across 5 Decimal(20,8) values.
        if delta > Decimal("0.01"):
            raise ValueError(
                f"tlc_total ({self.tlc_total}) does not match the sum of TLC "
                f"components ({computed}). Delta: {delta}."
            )
        return self


class TLCBreakdownOutput(PGIBase):
    """Per-mode TLC breakdown map â€” one entry per evaluated sourcing mode.

    Sourcing modes evaluated: local_direct | international_direct |
    distributor | broker | contract_manufacturer  (Â§3.72).

    Not all modes will be present â€” Repo B only computes TLC for modes
    relevant to the part category and available candidate_vendors.
    """

    local_direct: Optional[TLCModeBreakdown] = Field(
        default=None,
        description="TLC breakdown for local direct manufacturer sourcing.",
    )
    international_direct: Optional[TLCModeBreakdown] = Field(
        default=None,
        description="TLC breakdown for international direct manufacturer sourcing.",
    )
    distributor: Optional[TLCModeBreakdown] = Field(
        default=None,
        description="TLC breakdown for distributor sourcing.",
    )
    broker: Optional[TLCModeBreakdown] = Field(
        default=None,
        description="TLC breakdown for broker / spot-market sourcing.",
    )
    contract_manufacturer: Optional[TLCModeBreakdown] = Field(
        default=None,
        description="TLC breakdown for contract manufacturer sourcing.",
    )

    @model_validator(mode="after")
    def at_least_one_mode(self) -> "TLCBreakdownOutput":
        present = [
            m for m in [
                self.local_direct,
                self.international_direct,
                self.distributor,
                self.broker,
                self.contract_manufacturer,
            ]
            if m is not None
        ]
        if not present:
            raise ValueError(
                "TLCBreakdownOutput must contain at least one sourcing mode."
            )
        return self


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class StrategyResponse(PGIBase):
    """Full response body for POST /api/v1/strategy (Â§5.5).

    recommended_mode: the sourcing mode with the lowest TLC per unit
    at the requested quantity (Â§3.72 vocabulary).

    tlc_breakdown_json: per-mode TLC math.  Stored as
    Strategy_Recommendation.tlc_breakdown_json (justified JSONB, Â§2.26).

    q_break: the quantity crossover point at which switching to a lower-cost
    sourcing mode becomes economically optimal.  Null when only one mode is
    evaluated or no crossover exists within a practical quantity range.
    Stored as Strategy_Recommendation.q_break (Â§2.26).

    rationale: plain-language explanation of the recommendation (LAW-2).
    Stored as Strategy_Recommendation.rationale (Â§2.26).

    Repo C post-processing:
    1. Persists response as a Strategy_Recommendation row (Â§2.26).
    2. Updates BOM_Line.sourcing_type with the recommended_mode.
    3. Transitions BOM_Line.status: SCORED â†’ RFQ_PENDING (after buyer review).
    """

    recommended_mode: SourcingMode = Field(
        description=(
            "Lowest-TLC sourcing mode at the requested quantity. "
            "Vocabulary: local_direct | international_direct | distributor | "
            "broker | contract_manufacturer  (Â§3.72, Â§2.26)."
        ),
    )
    tlc_breakdown_json: TLCBreakdownOutput = Field(
        description=(
            "Per-mode TLC math (all evaluated modes). "
            "Stored in Strategy_Recommendation.tlc_breakdown_json (Â§2.26)."
        ),
    )
    q_break: Optional[Decimal] = Field(
        default=None,
        ge=Decimal("0"),
        description=(
            "Quantity crossover point for sourcing mode switch. "
            "Null when no crossover exists or only one mode evaluated. "
            "Stored in Strategy_Recommendation.q_break (Â§2.26)."
        ),
    )
    rationale: str = Field(
        min_length=1,
        description=(
            "Plain-language rationale for the recommended sourcing mode (LAW-2). "
            "Stored in Strategy_Recommendation.rationale (Â§2.26)."
        ),
    )

    @model_validator(mode="after")
    def recommended_mode_has_breakdown(self) -> "StrategyResponse":
        """Validate that the recommended_mode has a TLC breakdown entry."""
        mode_map = {
            SourcingMode.LOCAL_DIRECT: self.tlc_breakdown_json.local_direct,
            SourcingMode.INTERNATIONAL_DIRECT: self.tlc_breakdown_json.international_direct,
            SourcingMode.DISTRIBUTOR: self.tlc_breakdown_json.distributor,
            SourcingMode.BROKER: self.tlc_breakdown_json.broker,
            SourcingMode.CONTRACT_MANUFACTURER: self.tlc_breakdown_json.contract_manufacturer,
        }
        if mode_map.get(self.recommended_mode) is None:
            raise ValueError(
                f"recommended_mode '{self.recommended_mode}' does not have a "
                f"corresponding entry in tlc_breakdown_json."
            )
        return self


# --- replay endpoint contract ---

class ReplayRequest(PGIBase):
    """Full request body for POST /api/v1/replay (Â§5.6).

    bom_line_ids: the BOM_Line.bom_line_id UUIDs to re-normalize.
    Repo C batches these to match the /normalize batch limit (1â€“200).

    target_nlp_model_version: the new NLP model version to run against.
    Repo C validates this exists in Config_Version before dispatching.
    A mismatch with the active Repo B model returns 422.

    Note: Repo B does NOT query the database â€” it receives raw_text per
    bom_line_id implicitly through the Normalization_Trace history that
    Repo C looks up and includes.  In practice, Repo C supplies the
    raw_text in a sibling payload injected by the replay_worker (not shown
    here â€” replay_worker assembles the full Repo B payload from persisted data).
    """

    bom_line_ids: list[UUID] = Field(
        min_length=1,
        max_length=200,
        description=(
            "BOM_Line.bom_line_id UUIDs to re-normalize in this batch "
            "(1â€“200 per call)."
        ),
    )
    target_nlp_model_version: NLPModelVersion = Field(
        description=(
            "Target NLP model version to replay normalization against. "
            "Must match the active Repo B model or 422 is returned."
        ),
    )
    correlation_id: CorrelationID = Field(
        description="W3C-compatible correlation ID from the admin replay trigger.",
    )

    @model_validator(mode="after")
    def no_duplicate_ids(self) -> "ReplayRequest":
        """Validate that bom_line_ids contains no duplicates."""
        if len(self.bom_line_ids) != len(set(self.bom_line_ids)):
            seen: set[UUID] = set()
            dupes = [
                str(uid)
                for uid in self.bom_line_ids
                if uid in seen or seen.add(uid)  # type: ignore[func-returns-value]
            ]
            raise ValueError(
                f"bom_line_ids must be unique. Duplicate IDs: {dupes}."
            )
        return self


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response sub-models
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ReplayCanonicalSnapshot(CanonicalOutput):
    """A canonical output snapshot used in a replay result.

    Extends CanonicalOutput â€” no additional fields.  Subclassed for
    semantic clarity between ``previous_canonical`` and ``new_canonical``
    in replay diff results.
    """

    pass


class ReplayResult(PGIBase):
    """Replay normalization result for a single BOM line (Â§5.6).

    bom_line_id: echoed from ReplayRequest.bom_line_ids for correlation.

    previous_canonical: the canonical output currently persisted on the
    BOM line (as seen by Repo B from the raw_text + Normalization_Trace
    context supplied by Repo C â€” Repo B reconstructs it from the same input).

    new_canonical: the canonical output produced by the target NLP model
    version.

    confidence_delta: new_confidence âˆ’ previous_confidence.
    Positive = new model is more confident.
    Negative = new model is less confident (may increase NEEDS_REVIEW routing).

    diff_json: structured diff between previous and new canonical.
    Schema of diff_json is variable (justified per Â§2.93 analogy â€” diff
    structure depends on which canonical fields changed).

    significant_change: Repo B sets this to True when the canonical diff
    crosses a materiality threshold (e.g. part_name changed, category
    changed, or confidence_delta < âˆ’0.1).
    Repo C uses significant_change to gate NEEDS_REVIEW routing â€” if True,
    BOM_Line.status is reverted to NEEDS_REVIEW regardless of prior status.

    merged_with: echoed from the new normalization output (CN-15 â€” Repo C
    creates normalization_trace_merge rows, never UUID[] columns).
    split_from: echoed when the new model split this line differently.
    """

    bom_line_id: UUID = Field(
        description="Echoed BOM_Line.bom_line_id for correlation.",
    )
    previous_canonical: ReplayCanonicalSnapshot = Field(
        description=(
            "Canonical output currently persisted on the BOM line "
            "(reconstructed by Repo B from the same raw_text input)."
        ),
    )
    new_canonical: ReplayCanonicalSnapshot = Field(
        description="Canonical output produced by the target NLP model version.",
    )
    previous_confidence: Confidence3 = Field(
        description="Confidence score from the previous normalization run.",
    )
    new_confidence: Confidence3 = Field(
        description="Confidence score from the replay normalization.",
    )
    confidence_delta: Decimal = Field(
        ge=Decimal("-1.0"),
        le=Decimal("1.0"),
        description=(
            "new_confidence âˆ’ previous_confidence. "
            "Positive = improved; negative = degraded."
        ),
    )
    diff_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured diff between previous_canonical and new_canonical. "
            "Keys are canonical field names; values are "
            "{previous: <old_val>, new: <new_val>} objects. "
            "Empty dict when no fields changed."
        ),
    )
    significant_change: bool = Field(
        description=(
            "True when the canonical diff crosses Repo B's materiality threshold "
            "(e.g. part_name or category changed, or confidence_delta < âˆ’0.1). "
            "Repo C routes the BOM line to NEEDS_REVIEW when True."
        ),
    )
    new_decision: NormalizationDecisionType = Field(
        description=(
            "Normalization decision from the replay run. "
            "Repo C uses this alongside significant_change to determine "
            "whether to auto-commit or route to review."
        ),
    )
    merged_with: list[UUID] = Field(
        default_factory=list,
        description=(
            "BOM line IDs merged into this result by the replay. "
            "Repo C creates normalization_trace_merge rows (CN-15)."
        ),
    )
    split_from: Optional[UUID] = Field(
        default=None,
        description=(
            "Parent BOM_Line.bom_line_id if this line was split by the replay run."
        ),
    )

    @model_validator(mode="after")
    def confidence_delta_is_consistent(self) -> "ReplayResult":
        """Validate confidence_delta = new_confidence âˆ’ previous_confidence."""
        computed_delta = self.new_confidence - self.previous_confidence
        if abs(float(computed_delta - self.confidence_delta)) > 0.001:
            raise ValueError(
                f"confidence_delta ({self.confidence_delta}) must equal "
                f"new_confidence ({self.new_confidence}) âˆ’ "
                f"previous_confidence ({self.previous_confidence}) = "
                f"{computed_delta}."
            )
        return self


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Response
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ReplayResponse(PGIBase):
    """Full response body for POST /api/v1/replay (Â§5.6).

    nlp_model_version: the active NLP model version on Repo B at call time.
    Repo C stores this on the Normalization_Run.nlp_model_version created
    for this replay batch.

    results: one ReplayResult per bom_line_id in the request.
    Order is not guaranteed â€” Repo C correlates by bom_line_id.

    Repo C post-processing per result:
    1. Appends Normalization_Trace row (APPEND-ONLY â€” Â§2.10).
    2. Creates normalization_trace_merge rows for merged_with (CN-15).
    3. Updates BOM_Line.normalization_confidence = result.new_confidence.
    4. If significant_change == True â†’ BOM_Line.status â†’ NEEDS_REVIEW.
    5. If significant_change == False and new_confidence >= 0.85 â†’
       BOM_Line.status remains NORMALIZED (or current); no NEEDS_REVIEW.
    6. Writes Event_Audit_Log entry per BOM line transitioned.
    7. Updates Normalization_Run.output_count and completed_at on batch end.
    """

    nlp_model_version: NLPModelVersion = Field(
        description=(
            "Active NLP model version stamp from Repo B. "
            "Stored on the Normalization_Run for this replay batch."
        ),
    )
    results: list[ReplayResult] = Field(
        min_length=1,
        description=(
            "One replay result per input bom_line_id. "
            "Order is not guaranteed â€” correlate by bom_line_id."
        ),
    )

    @model_validator(mode="after")
    def results_non_empty(self) -> "ReplayResponse":
        if not self.results:
            raise ValueError(
                "ReplayResponse.results must contain at least one entry."
            )
        return self

__all__ = [
    "RepoBDeliveryLocation",
    "CanonicalOutput",
    "BaselinePriceContext",
    "TariffSnapshotContext",
    "LogisticsSnapshotContext",
    "ForexSnapshotContext",
    "MarketContextPayload",
    "VendorCandidateProfile",
    "VendorCandidateCapability",
    "VendorPerformanceSnapshot",
    "VendorCandidatePayload",
    "CostBand",
    "StrategyVendorInput",
    "PartMasterHint",
    "NormalizeRowInput",
    "NormalizeRequest",
    "NormalizeCanonicalOutput",
    "NormalizePartMasterCandidate",
    "NormalizeResult",
    "NormalizeResponse",
    "EnrichBOMLineInput",
    "EnrichRequest",
    "EnrichPriceBandResult",
    "EnrichTariffResult",
    "EnrichLogisticsCostBand",
    "EnrichLogisticsResult",
    "EnrichmentOutput",
    "EnrichResponse",
    "ScoreBOMLineInput",
    "ScoreRequest",
    "ScoreDimensionBreakdown",
    "ScoreBreakdownMap",
    "RankedVendorResult",
    "ScoreResponse",
    "StrategyBOMLineInput",
    "StrategyRequest",
    "TLCModeBreakdown",
    "TLCBreakdownOutput",
    "StrategyResponse",
    "ReplayRequest",
    "ReplayCanonicalSnapshot",
    "ReplayResult",
    "ReplayResponse",
]

