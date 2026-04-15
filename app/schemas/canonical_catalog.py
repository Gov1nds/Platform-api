"""
Phase 2B Batch 1B DTOs for catalog discovery and source SKU linking.

These DTOs are additive and do not change Phase 2A DTO behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class CatalogSearchCandidate:
    source_system: str
    external_sku_id: str
    manufacturer: str | None = None
    mpn: str | None = None
    normalized_mpn: str | None = None
    vendor_sku: str | None = None
    vendor_id: str | None = None
    canonical_part_key: str | None = None
    link_method: str = "external_id"
    link_confidence: Decimal = Decimal("0")
    is_ambiguous: bool = False
    part_to_sku_mapping_id: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CatalogDiscoveryResult:
    canonical_part_key: str
    reused_existing_links: bool = False
    connector_called: bool = False
    ambiguous: bool = False
    discarded_candidates: int = 0
    discovered_candidate_count: int = 0
    canonical_sku_ids: list[str] = field(default_factory=list)
    source_sku_link_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)