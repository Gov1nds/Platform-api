"""
Catalog Ingestion service.

Implements Execution Plan §8 "Part/Vendor Catalogs: ingest via API to
extract offering, pricing, lead-time. Map catalog SKUs to canonical parts."

Input is a list of catalog line-item dicts. The service:
 1. Normalizes each line.
 2. Maps to a canonical_part_key (fuzzy match against existing BOM parts
    / canonical SKUs when present; otherwise hashed canonical key).
 3. Creates / updates a PartVendorIndex row with last_quote_price and
    last_quote_date from the catalog entry.
 4. Creates / updates a VendorLeadTimeBand when lead-time info is present.
 5. Triggers award_ready re-check via PartVendorMatcher.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.canonical import CanonicalSKU
from app.models.matching import PartVendorIndex
from app.models.vendor import VendorLeadTimeBand
from app.services.matching.part_vendor_matcher import part_vendor_matcher

logger = logging.getLogger(__name__)


@dataclass
class CatalogIngestionResult:
    vendor_id: str
    items_processed: int = 0
    mapped_to_canonical: int = 0
    unmapped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "items_processed": self.items_processed,
            "mapped_to_canonical": self.mapped_to_canonical,
            "unmapped": self.unmapped,
            "errors": list(self.errors),
        }


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _trim(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_description(value: str) -> str:
    s = str(value).strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _hash_canonical_key(description: str, category_tag: str | None) -> str:
    basis = f"{_normalize_description(description)}|{(category_tag or '').lower()}"
    return "auto:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


class CatalogIngestionService:
    """§8 catalog ingestion."""

    def ingest_catalog_for_vendor(
        self,
        vendor_id: str,
        catalog_data: Iterable[dict[str, Any]],
        db: Session,
    ) -> CatalogIngestionResult:
        result = CatalogIngestionResult(vendor_id=vendor_id)

        # Preload CanonicalSKU rows for fuzzy name lookups (best-effort).
        canonical_index = self._load_canonical_index(db)

        for item in catalog_data:
            try:
                processed = self._process_item(
                    vendor_id=vendor_id,
                    item=item,
                    db=db,
                    canonical_index=canonical_index,
                )
                result.items_processed += 1
                if processed.get("mapped"):
                    result.mapped_to_canonical += 1
                else:
                    result.unmapped += 1
            except Exception as exc:
                logger.exception("catalog item failed for vendor=%s", vendor_id)
                result.errors.append(f"item_error: {exc}")

        db.flush()
        logger.info(
            "catalog_ingest vendor=%s processed=%d mapped=%d",
            vendor_id, result.items_processed, result.mapped_to_canonical,
        )
        return result

    # ── Internals ───────────────────────────────────────────────────────

    def _process_item(
        self,
        vendor_id: str,
        item: dict[str, Any],
        db: Session,
        canonical_index: list[tuple[str, str]],
    ) -> dict[str, Any]:
        sku = _trim(item.get("sku"))
        description = _trim(item.get("description")) or sku
        if not description:
            raise ValueError("catalog item requires 'sku' or 'description'")

        category_tag = _trim(item.get("category_tag")) or _trim(item.get("category")) or None
        material_family = _trim(item.get("material_family")) or None
        unit_price = _decimal_or_none(item.get("unit_price"))
        currency = (_trim(item.get("currency")) or "USD")[:3].upper()
        moq = _decimal_or_none(item.get("moq"))
        lt_days = _decimal_or_none(item.get("lead_time_days"))
        lt_min = _int_or_none(item.get("lead_time_min_days"))
        lt_max = _int_or_none(item.get("lead_time_max_days"))

        canonical_part_key, mapped = self._map_to_canonical(
            description=description,
            category_tag=category_tag,
            canonical_index=canonical_index,
        )

        # Upsert PartVendorIndex
        row = (
            db.query(PartVendorIndex)
            .filter(
                PartVendorIndex.canonical_part_key == canonical_part_key,
                PartVendorIndex.vendor_id == vendor_id,
            )
            .first()
        )
        if row is None:
            row = PartVendorIndex(
                canonical_part_key=canonical_part_key,
                vendor_id=vendor_id,
                match_type="partial_category" if mapped else "historical",
                match_score=Decimal("0.5000") if mapped else Decimal("0.3500"),
            )
            db.add(row)

        if unit_price is not None:
            row.last_quote_price = unit_price
            row.last_quote_currency = currency
            row.last_quote_date = _today()
        row.evidence_count = int(row.evidence_count or 0) + 1
        row.last_updated_at = datetime.now(timezone.utc)
        history = list(row.historical_evidence or [])
        history.append(
            {
                "type": "catalog",
                "sku": sku,
                "description": description[:120],
                "unit_price": str(unit_price) if unit_price is not None else None,
                "currency": currency,
                "at": _today().isoformat(),
            }
        )
        row.historical_evidence = history[-25:]

        # Upsert lead-time band
        if lt_days is not None or lt_min is not None or lt_max is not None or moq is not None:
            band = (
                db.query(VendorLeadTimeBand)
                .filter(
                    VendorLeadTimeBand.vendor_id == vendor_id,
                    VendorLeadTimeBand.category_tag == category_tag,
                )
                .first()
            )
            if band is None:
                band = VendorLeadTimeBand(
                    vendor_id=vendor_id,
                    category_tag=category_tag,
                    material_family=material_family,
                    source="catalog",
                    confidence=Decimal("0.80"),
                )
                db.add(band)
            if lt_days is not None:
                band.lead_time_typical_days = lt_days
            if lt_min is not None:
                band.lead_time_min_days = lt_min
            if lt_max is not None:
                band.lead_time_max_days = lt_max
            if moq is not None:
                band.moq = moq

        # Recompute award-ready via the matcher's outcome hook.
        try:
            part_vendor_matcher.update_index_from_outcome(
                canonical_part_key=canonical_part_key,
                vendor_id=vendor_id,
                outcome_type="rfq_response",
                outcome_data={
                    "quoted_price": str(unit_price) if unit_price is not None else None,
                    "currency": currency,
                    "quote_date": _today().isoformat(),
                    "source": "catalog",
                },
                db=db,
            )
        except Exception:
            logger.exception(
                "update_index_from_outcome failed for vendor=%s part=%s", vendor_id, canonical_part_key
            )
        return {"mapped": mapped, "canonical_part_key": canonical_part_key}

    def _load_canonical_index(self, db: Session) -> list[tuple[str, str]]:
        """Load (normalized_description, canonical_part_key) pairs once per batch."""
        try:
            rows = db.query(CanonicalSKU).limit(2000).all()
        except Exception:
            return []
        index: list[tuple[str, str]] = []
        for r in rows:
            # Prefer a human-friendly name for fuzzy matching; fall back on id
            name = getattr(r, "canonical_name", None) or getattr(r, "description", None) or ""
            key = getattr(r, "canonical_key", None) or getattr(r, "id", None)
            if name and key:
                index.append((_normalize_description(str(name)), str(key)))
        return index

    def _map_to_canonical(
        self,
        description: str,
        category_tag: str | None,
        canonical_index: list[tuple[str, str]],
    ) -> tuple[str, bool]:
        norm = _normalize_description(description)
        if not norm:
            return _hash_canonical_key(description, category_tag), False

        # Fuzzy match against canonical_index
        best_ratio = 0.0
        best_key: str | None = None
        for idx_norm, idx_key in canonical_index:
            if not idx_norm:
                continue
            r = difflib.SequenceMatcher(None, norm, idx_norm).ratio()
            if r > best_ratio:
                best_ratio = r
                best_key = idx_key
        if best_key and best_ratio >= 0.82:
            return best_key, True
        return _hash_canonical_key(description, category_tag), False


catalog_ingestion_service = CatalogIngestionService()
