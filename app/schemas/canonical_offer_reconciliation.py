"""
Phase 2B Batch 1C DTOs for multi-source offer reconciliation.

These DTOs are additive and do not change Phase 2A ingestion DTO behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class ReconciledOfferCandidate:
    canonical_sku_id: str
    source_sku_link_id: str | None
    sku_offer_id: str
    external_offer_id: str | None
    source_system: str
    vendor_id: str | None
    currency: str
    normalized_currency: str
    uom: str | None
    normalized_uom: str | None
    unit_price: Decimal
    normalized_unit_price: Decimal
    break_qty: Decimal | None
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime | None
    freshness_status: str
    is_stale: bool
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalOfferReconciliationResult:
    canonical_sku_id: str
    as_of: datetime
    best_price: Decimal | None
    best_currency: str | None
    best_source_system: str | None
    best_external_offer_id: str | None
    best_sku_offer_id: str | None
    price_spread: Decimal | None
    offer_count: int
    freshness_minutes: int | None
    valid_through: datetime | None
    is_stale: bool
    has_conflict: bool
    snapshot_id: str | None = None
    candidate_offer_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)