"""
Phase 2A enrichment DTOs for connector and lookup abstractions.

These DTOs are intentionally provider-neutral. They let platform-api own the
workflow while keeping external provider logic abstract and swappable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class PartIdentity:
    bom_part_id: str
    canonical_part_key: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    normalized_mpn: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    category_code: str | None = None
    procurement_class: str | None = None
    specs: dict[str, Any] = field(default_factory=dict)
    normalization_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProductSearchCandidate:
    vendor_id: str | None
    vendor_sku: str
    manufacturer: str | None = None
    mpn: str | None = None
    normalized_mpn: str | None = None
    canonical_part_key: str | None = None
    match_method: str = "connector"
    match_score: Decimal = Decimal("0")
    mapping_status: str = "candidate"
    source_system: str = "connector"
    source_record_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    is_preferred: bool = False


@dataclass(slots=True)
class PriceBreakDTO:
    break_qty: Decimal
    unit_price: Decimal
    currency: str = "USD"
    price_type: str = "unit"
    extended_price: Decimal | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OfferDTO:
    vendor_id: str | None
    vendor_sku: str
    offer_name: str | None = None
    offer_status: str = "ACTIVE"
    currency: str = "USD"
    uom: str | None = None
    moq: Decimal | None = None
    spq: Decimal | None = None
    lead_time_days: Decimal | None = None
    packaging: str | None = None
    incoterm: str | None = None
    country_of_origin: str | None = None
    factory_region: str | None = None
    is_authorized: bool = False
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    ttl_seconds: int | None = None
    source_system: str = "connector"
    source_record_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    price_breaks: list[PriceBreakDTO] = field(default_factory=list)


@dataclass(slots=True)
class AvailabilityDTO:
    vendor_sku: str
    availability_status: str = "UNKNOWN"
    available_qty: Decimal | None = None
    on_order_qty: Decimal | None = None
    allocated_qty: Decimal | None = None
    backorder_qty: Decimal | None = None
    moq: Decimal | None = None
    factory_lead_time_days: Decimal | None = None
    inventory_location: str | None = None
    snapshot_at: datetime | None = None
    ttl_seconds: int | None = None
    source_system: str = "connector"
    source_record_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedOfferPrice:
    break_qty: Decimal
    unit_price: Decimal
    currency: str
    price_type: str
    extended_price: Decimal | None = None


@dataclass(slots=True)
class HSResolutionDTO:
    bom_part_id: str
    resolution_status: str
    resolved: bool
    hs_code: str | None = None
    hs_version: str | None = None
    jurisdiction: str | None = None
    confidence: Decimal = Decimal("0")
    mapping_method: str | None = None
    review_status: str | None = None
    matched_on: str | None = None
    source_system: str | None = None
    source_record_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    uncertainty_reason: str | None = None
    mapping_id: str | None = None


@dataclass(slots=True)
class TariffLookupDTO:
    bom_part_id: str | None
    resolved: bool
    lookup_status: str
    hs_code: str | None = None
    hs6: str | None = None
    hs_version: str | None = None
    national_extension_code: str | None = None
    tariff_code_type: str | None = None
    destination_country: str | None = None
    import_country: str | None = None
    origin_country: str | None = None
    lookup_date: datetime | None = None
    tariff_schedule_id: str | None = None
    duty_rate_pct: Decimal = Decimal("0")
    additional_taxes_pct: Decimal = Decimal("0")
    total_tariff_rate_pct: Decimal = Decimal("0")
    confidence: Decimal = Decimal("0")
    source: str | None = None
    freshness_status: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    coverage_level: str | None = None
    coverage_status: str | None = None
    last_ingested_at: datetime | None = None
    estimated_customs_value: Decimal | None = None
    estimated_duty: Decimal | None = None
    estimated_additional_taxes: Decimal | None = None
    estimated_total_tariff: Decimal | None = None
    uncertainty_reason: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LaneLookupContextDTO:
    origin_country: str | None = None
    origin_region: str | None = None
    destination_country: str | None = None
    destination_region: str | None = None
    mode: str | None = "sea"
    service_level: str | None = None
    weight_kg: Decimal | None = None
    volume_cbm: Decimal | None = None


@dataclass(slots=True)
class LaneRateLookupDTO:
    bom_part_id: str | None
    project_id: str | None
    resolved: bool
    lookup_status: str
    origin_country: str | None = None
    origin_region: str | None = None
    destination_country: str | None = None
    destination_region: str | None = None
    mode: str | None = None
    service_level: str | None = None
    lookup_date: datetime | None = None
    lane_key: str | None = None
    lane_rate_band_id: str | None = None
    currency: str | None = None
    rate_type: str | None = None
    rate_value: Decimal | None = None
    min_charge: Decimal | None = None
    p50_freight_estimate: Decimal | None = None
    p90_freight_estimate: Decimal | None = None
    transit_days_min: int | None = None
    transit_days_max: int | None = None
    confidence: Decimal = Decimal("0")
    freshness_status: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    coverage_status: str | None = None
    priority_tier: str | None = None
    refresh_cadence: str | None = None
    last_refreshed_at: datetime | None = None
    uncertainty_reason: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Phase2AEvidenceBundleDTO:
    bom_part_id: str
    offer_evidence: dict[str, Any] = field(default_factory=dict)
    availability_evidence: dict[str, Any] = field(default_factory=dict)
    tariff_evidence: dict[str, Any] = field(default_factory=dict)
    freight_evidence: dict[str, Any] = field(default_factory=dict)
    freshness_summary: dict[str, Any] = field(default_factory=dict)
    confidence_summary: dict[str, Any] = field(default_factory=dict)
    uncertainty_flags: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
