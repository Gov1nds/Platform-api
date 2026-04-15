"""
Phase 2B Batch 1D DTOs for multi-source availability reconciliation.

These DTOs are additive and do not change Phase 2A ingestion DTO behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class ReconciledAvailabilityCandidate:
    canonical_sku_id: str
    source_sku_link_id: str | None
    sku_offer_id: str
    availability_snapshot_id: str
    external_snapshot_id: str | None
    source_system: str
    availability_status: str
    available_qty: Decimal | None
    on_order_qty: Decimal | None
    allocated_qty: Decimal | None
    backorder_qty: Decimal | None
    lead_time_days: Decimal | None
    inventory_location: str | None
    snapshot_at: datetime | None
    freshness_status: str
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalAvailabilityReconciliationResult:
    canonical_sku_id: str
    as_of: datetime
    availability_status: str
    available_qty: Decimal | None
    lead_time_days: Decimal | None
    source_systems: list[str] = field(default_factory=list)
    freshness_minutes: int | None = None
    snapshot_id: str | None = None
    candidate_snapshot_ids: list[str] = field(default_factory=list)
    has_conflict: bool = False
    notes: list[str] = field(default_factory=list)