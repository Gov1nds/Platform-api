"""
Phase 2A Batch 2 DTOs for enrichment connector abstractions.

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