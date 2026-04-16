"""
Part → Vendor Matching Engine.

Implements Execution Plan §2: normalize BOM line → canonical part, then
match against vendors along three axes:

  1. Category / capability fit (exact vs partial).
  2. Historical evidence boost (past quotes, POs, alias cross-refs).
  3. RFQ-first vs award-ready classification.

Persists results into pricing.part_vendor_index so the system builds an
accumulating learning store. Each new RFQ or PO outcome feeds back via
update_index_from_outcome().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.matching import PartVendorIndex
from app.models.vendor import (
    Vendor, VendorCapability, VendorIdentityAlias, VendorLeadTimeBand,
    VendorPerformanceSnapshot,
)

logger = logging.getLogger(__name__)


TIER_SCORE_MULTIPLIER = {
    "PLATINUM":  1.00,
    "GOLD":      0.95,
    "SILVER":    0.85,
    "BRONZE":    0.70,
    "UNVERIFIED": 0.50,
}

MATCH_TYPE_EXACT = "exact_category"
MATCH_TYPE_PARTIAL = "partial_category"
MATCH_TYPE_HISTORICAL = "historical"
MATCH_TYPE_ALIAS = "alias"
MATCH_TYPE_NONE = "no_match"


@dataclass
class PartVendorMatchResult:
    vendor_id: str
    canonical_part_key: str
    match_type: str
    match_score: float
    confidence: float
    award_ready: bool
    rfq_first_recommended: bool
    evidence_count: int
    category_match: dict[str, Any] = field(default_factory=dict)
    material_match: dict[str, Any] = field(default_factory=dict)
    process_match: dict[str, Any] = field(default_factory=dict)
    alias_match: dict[str, Any] = field(default_factory=dict)
    historical: dict[str, Any] = field(default_factory=dict)
    last_quote_price: Decimal | None = None
    last_quote_currency: str | None = None
    last_quote_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "canonical_part_key": self.canonical_part_key,
            "match_type": self.match_type,
            "match_score": round(self.match_score, 4),
            "confidence": round(self.confidence, 4),
            "award_ready": bool(self.award_ready),
            "rfq_first_recommended": bool(self.rfq_first_recommended),
            "evidence_count": self.evidence_count,
            "category_match": dict(self.category_match),
            "material_match": dict(self.material_match),
            "process_match": dict(self.process_match),
            "alias_match": dict(self.alias_match),
            "historical": dict(self.historical),
            "last_quote_price": str(self.last_quote_price) if self.last_quote_price is not None else None,
            "last_quote_currency": self.last_quote_currency,
            "last_quote_date": self.last_quote_date.isoformat() if self.last_quote_date else None,
        }


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _lower_set(values: Iterable[Any]) -> set[str]:
    return {str(v).strip().lower() for v in values if v is not None and str(v).strip()}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class PartVendorMatcher:
    """Build part↔vendor match rows implementing §2 of the execution plan."""

    # ── Capability fit ───────────────────────────────────────────────────

    def _capability_fit(
        self,
        vendor_capabilities: list[dict[str, Any]],
        vendor_primary_category: str | None,
        vendor_secondary_categories: list[str] | None,
        part_category: str | None,
        part_material: str | None,
        part_processes: list[str] | None,
    ) -> tuple[float, str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        """
        Return (score, match_type, category_detail, material_detail, process_detail).
        """
        cap_processes = _lower_set(c.get("process") for c in vendor_capabilities)
        cap_materials = _lower_set(c.get("material_family") for c in vendor_capabilities)
        vendor_cats = _lower_set(
            [vendor_primary_category] + list(vendor_secondary_categories or [])
        )

        part_cat = (part_category or "").strip().lower()
        part_mat = (part_material or "").strip().lower()
        part_procs = _lower_set(part_processes or [])

        process_match_ratio = 0.0
        if part_procs:
            hit = len(part_procs & cap_processes)
            process_match_ratio = hit / max(1, len(part_procs))
        elif cap_processes:
            process_match_ratio = 0.5  # unspecified procs — neutral

        material_match = False
        if part_mat:
            material_match = any(
                part_mat in cm or cm in part_mat for cm in cap_materials
            )
        else:
            material_match = bool(cap_materials)

        category_match = False
        if part_cat:
            category_match = any(
                part_cat in cat or cat in part_cat for cat in vendor_cats
            )
        else:
            category_match = bool(vendor_cats)

        # Exact: category AND material AND processes all align
        if (
            category_match
            and material_match
            and process_match_ratio >= 0.99
            and part_procs
        ):
            return (
                1.0,
                MATCH_TYPE_EXACT,
                {"match": True, "kind": "exact", "vendor_categories": sorted(vendor_cats)},
                {"match": True, "part": part_mat or None, "vendor_has_material": True},
                {"match_ratio": round(process_match_ratio, 4), "matched": list(part_procs & cap_processes)},
            )

        # Partial: at least one of category/material/process signals align
        partial_hits = sum([category_match, material_match, process_match_ratio > 0])
        if partial_hits >= 1:
            score = 0.0
            if category_match:
                score += 0.25
            if material_match:
                score += 0.15
            score += 0.35 * min(1.0, process_match_ratio)
            # At least capability signals exist — record 0.5 floor
            score = max(score, 0.5 if partial_hits >= 2 else score)
            score = min(score, 0.95)  # never reach 1.0 without exact alignment
            return (
                round(score, 4),
                MATCH_TYPE_PARTIAL,
                {"match": bool(category_match), "kind": "partial", "vendor_categories": sorted(vendor_cats)},
                {"match": bool(material_match), "part": part_mat or None},
                {"match_ratio": round(process_match_ratio, 4), "matched": list(part_procs & cap_processes)},
            )

        return (
            0.0,
            MATCH_TYPE_NONE,
            {"match": False, "vendor_categories": sorted(vendor_cats)},
            {"match": False, "part": part_mat or None},
            {"match_ratio": 0.0, "matched": []},
        )

    # ── Historical evidence ──────────────────────────────────────────────

    def _historical_boost(
        self,
        db: Session,
        canonical_part_key: str,
        vendor_id: str,
    ) -> tuple[float, dict[str, Any]]:
        """Boost from prior PartVendorIndex state + alias matches."""
        existing = (
            db.query(PartVendorIndex)
            .filter(
                PartVendorIndex.canonical_part_key == canonical_part_key,
                PartVendorIndex.vendor_id == vendor_id,
            )
            .first()
        )

        boost = 0.0
        evidence: dict[str, Any] = {
            "evidence_count": 0,
            "po_win_count": 0,
            "rfq_sent_count": 0,
            "last_quote_date": None,
            "last_po_date": None,
            "boost_applied": 0.0,
        }

        if existing:
            ev = int(existing.evidence_count or 0)
            wins = int(existing.po_win_count or 0)
            evidence["evidence_count"] = ev
            evidence["po_win_count"] = wins
            evidence["rfq_sent_count"] = int(existing.rfq_sent_count or 0)
            evidence["last_quote_date"] = (
                existing.last_quote_date.isoformat() if existing.last_quote_date else None
            )
            evidence["last_po_date"] = (
                existing.last_po_date.isoformat() if existing.last_po_date else None
            )

            if ev >= 3 and wins >= 1:
                boost += 0.20
            elif ev >= 1:
                boost += 0.10

            if existing.last_quote_date and (_today() - existing.last_quote_date).days <= 90:
                boost += 0.05

        # Alias cross-reference: if any of the vendor's aliases mention the
        # canonical part key tokens, add small extra boost.
        alias_hit = self._alias_boost(db, canonical_part_key, vendor_id)
        if alias_hit["matched"]:
            boost += 0.08

        evidence["boost_applied"] = round(boost, 4)
        evidence["alias_match"] = alias_hit
        return boost, evidence

    def _alias_boost(
        self,
        db: Session,
        canonical_part_key: str,
        vendor_id: str,
    ) -> dict[str, Any]:
        if not canonical_part_key:
            return {"matched": False, "aliases_checked": 0}
        tokens = {t for t in canonical_part_key.lower().replace("_", " ").split() if len(t) > 2}
        aliases = (
            db.query(VendorIdentityAlias)
            .filter(
                VendorIdentityAlias.vendor_id == vendor_id,
                VendorIdentityAlias.is_active.is_(True),
            )
            .limit(50)
            .all()
        )
        hits: list[str] = []
        for alias in aliases:
            nv = (alias.normalized_value or "").lower()
            if not nv:
                continue
            for tok in tokens:
                if tok in nv:
                    hits.append(alias.normalized_value)
                    break
        return {
            "matched": bool(hits),
            "aliases_checked": len(aliases),
            "matched_aliases": hits[:5],
        }

    # ── Award-ready gate ─────────────────────────────────────────────────

    def _classify_award_ready(
        self,
        db: Session,
        vendor: dict[str, Any],
        capability_score: float,
        existing_index: PartVendorIndex | None,
    ) -> tuple[bool, bool, list[str]]:
        """
        Return (award_ready, rfq_first_recommended, reasons).

        Rule set (all must hold for award_ready=True):
          - capability_score >= 0.70
          - vendor has lead time data (VendorLeadTimeBand or avg_lead_time_days)
          - last_quote_price is not None AND not older than 180 days
          - vendor reliability >= 0.70
          - evidence_count >= 2
        """
        reasons: list[str] = []

        if capability_score < 0.70:
            reasons.append("capability_score_below_0.70")

        has_lt = bool(vendor.get("avg_lead_time_days")) or db.query(
            VendorLeadTimeBand.id
        ).filter(VendorLeadTimeBand.vendor_id == vendor["id"]).first() is not None
        if not has_lt:
            reasons.append("no_lead_time_data")

        fresh_quote = False
        if existing_index and existing_index.last_quote_price is not None and existing_index.last_quote_date:
            age_days = (_today() - existing_index.last_quote_date).days
            if age_days <= 180:
                fresh_quote = True
        if not fresh_quote:
            reasons.append("no_fresh_quote_in_180_days")

        reliability = _as_float(vendor.get("reliability_score"), 0.0)
        latest_snap = (
            db.query(VendorPerformanceSnapshot)
            .filter(VendorPerformanceSnapshot.vendor_id == vendor["id"])
            .order_by(VendorPerformanceSnapshot.snapshot_date.desc())
            .first()
        )
        if latest_snap and latest_snap.on_time_delivery_pct is not None:
            reliability = _as_float(latest_snap.on_time_delivery_pct, reliability)
        if reliability < 0.70:
            reasons.append("reliability_below_0.70")

        evidence_count = int(existing_index.evidence_count if existing_index else 0)
        if evidence_count < 2:
            reasons.append("evidence_count_below_2")

        award_ready = not reasons
        rfq_first = not award_ready
        return award_ready, rfq_first, reasons

    # ── Public: single-part match ────────────────────────────────────────

    def match_canonical_part(
        self,
        canonical_part: dict[str, Any],
        db: Session,
        vendor_pool: Iterable[dict[str, Any]] | None = None,
        persist: bool = True,
    ) -> list[PartVendorMatchResult]:
        canonical_part_key = str(canonical_part.get("canonical_part_key") or "").strip()
        if not canonical_part_key:
            logger.warning("match_canonical_part: missing canonical_part_key")
            return []

        part_category = canonical_part.get("category_tag") or canonical_part.get("procurement_class")
        part_material = canonical_part.get("material_family") or canonical_part.get("material")
        part_processes = canonical_part.get("processes") or []
        if isinstance(part_processes, str):
            part_processes = [part_processes]

        if vendor_pool is None:
            vendor_pool = self._load_active_vendor_pool(db)

        results: list[PartVendorMatchResult] = []

        for vendor in vendor_pool:
            vendor_id = vendor["id"]
            caps = vendor.get("capabilities") or []

            cap_score, match_type, cat_detail, mat_detail, proc_detail = self._capability_fit(
                vendor_capabilities=caps,
                vendor_primary_category=vendor.get("primary_category_tag"),
                vendor_secondary_categories=vendor.get("secondary_category_tags") or [],
                part_category=part_category,
                part_material=part_material,
                part_processes=part_processes,
            )

            tier = (vendor.get("trust_tier") or "UNVERIFIED").upper()
            tier_mult = TIER_SCORE_MULTIPLIER.get(tier, 0.50)
            base_score = cap_score * tier_mult

            boost, hist = self._historical_boost(db, canonical_part_key, vendor_id)
            match_score = min(1.0, base_score + boost)

            # Upgrade match_type if historical provides the dominant signal
            if match_type == MATCH_TYPE_NONE and boost > 0:
                match_type = MATCH_TYPE_HISTORICAL
            elif hist.get("alias_match", {}).get("matched") and match_type == MATCH_TYPE_NONE:
                match_type = MATCH_TYPE_ALIAS

            if match_score <= 0.0 and not hist.get("alias_match", {}).get("matched"):
                # Pointless to keep zero-scoring vendors; do not persist.
                continue

            existing_idx = (
                db.query(PartVendorIndex)
                .filter(
                    PartVendorIndex.canonical_part_key == canonical_part_key,
                    PartVendorIndex.vendor_id == vendor_id,
                )
                .first()
            )

            award_ready, rfq_first, gate_reasons = self._classify_award_ready(
                db=db,
                vendor=vendor,
                capability_score=cap_score,
                existing_index=existing_idx,
            )

            confidence = self._confidence_from(match_score, hist.get("evidence_count", 0), tier)

            result = PartVendorMatchResult(
                vendor_id=vendor_id,
                canonical_part_key=canonical_part_key,
                match_type=match_type,
                match_score=match_score,
                confidence=confidence,
                award_ready=award_ready,
                rfq_first_recommended=rfq_first,
                evidence_count=int(hist.get("evidence_count", 0)),
                category_match=cat_detail,
                material_match=mat_detail,
                process_match=proc_detail,
                alias_match=hist.get("alias_match", {}),
                historical={
                    **hist,
                    "award_ready_gate_reasons": gate_reasons,
                    "trust_tier_multiplier": tier_mult,
                    "trust_tier": tier,
                },
                last_quote_price=existing_idx.last_quote_price if existing_idx else None,
                last_quote_currency=existing_idx.last_quote_currency if existing_idx else None,
                last_quote_date=existing_idx.last_quote_date if existing_idx else None,
            )

            if persist:
                self._upsert_index(
                    db, result,
                    existing=existing_idx,
                )

            results.append(result)

        results.sort(key=lambda r: r.match_score, reverse=True)
        return results

    # ── Batch build ──────────────────────────────────────────────────────

    def build_part_vendor_index_for_bom(
        self,
        bom_id: str,
        db: Session,
    ) -> dict[str, Any]:
        from app.models.bom import BOMPart  # local import avoids circulars
        parts = (
            db.query(BOMPart)
            .filter(BOMPart.bom_id == bom_id, BOMPart.deleted_at.is_(None))
            .all()
        )
        vendor_pool = self._load_active_vendor_pool(db)

        total_parts = 0
        total_rows = 0
        for part in parts:
            canonical_key = (
                part.canonical_part_key
                or part.normalized_text
                or part.description
                or part.raw_text
                or ""
            )
            if not canonical_key:
                continue
            total_parts += 1
            matches = self.match_canonical_part(
                canonical_part={
                    "canonical_part_key": canonical_key,
                    "category_tag": part.procurement_class,
                    "material_family": part.material,
                    "processes": [part.procurement_class] if part.procurement_class else [],
                },
                db=db,
                vendor_pool=vendor_pool,
                persist=True,
            )
            total_rows += len(matches)

        db.flush()
        logger.info(
            "build_part_vendor_index_for_bom bom=%s parts=%d rows=%d",
            bom_id, total_parts, total_rows,
        )
        return {"bom_id": bom_id, "parts_processed": total_parts, "rows_upserted": total_rows}

    # ── Outcome-driven update ────────────────────────────────────────────

    def update_index_from_outcome(
        self,
        canonical_part_key: str,
        vendor_id: str,
        outcome_type: str,
        outcome_data: dict[str, Any],
        db: Session,
    ) -> PartVendorIndex:
        """
        outcome_type ∈ {rfq_sent, rfq_response, po_awarded, po_delivered, po_failed}
        outcome_data keys (all optional): quoted_price, currency, quote_date,
          po_date, actual_lead_time, quality_passed.
        """
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
                match_score=Decimal("0.2000"),
                match_type=MATCH_TYPE_HISTORICAL,
            )
            db.add(row)

        ev = int(row.evidence_count or 0)
        hist = list(row.historical_evidence or [])

        if outcome_type == "rfq_sent":
            row.rfq_sent_count = int(row.rfq_sent_count or 0) + 1
        elif outcome_type == "rfq_response":
            price = outcome_data.get("quoted_price")
            if price is not None:
                row.last_quote_price = Decimal(str(price))
            if outcome_data.get("currency"):
                row.last_quote_currency = str(outcome_data["currency"]).upper()[:3]
            qd = outcome_data.get("quote_date")
            if qd:
                row.last_quote_date = _coerce_date(qd)
            ev += 1
            hist.append({"type": "rfq_response", "at": (_today()).isoformat(), **outcome_data})
        elif outcome_type == "po_awarded":
            row.po_win_count = int(row.po_win_count or 0) + 1
            pod = outcome_data.get("po_date")
            if pod:
                row.last_po_date = _coerce_date(pod)
            ev += 1
            hist.append({"type": "po_awarded", "at": (_today()).isoformat(), **outcome_data})
        elif outcome_type == "po_delivered":
            ev += 1
            hist.append({"type": "po_delivered", "at": (_today()).isoformat(), **outcome_data})
        elif outcome_type == "po_failed":
            hist.append({"type": "po_failed", "at": (_today()).isoformat(), **outcome_data})
        else:
            logger.warning("update_index_from_outcome: unknown outcome_type=%s", outcome_type)

        row.evidence_count = ev
        row.historical_evidence = hist[-25:]  # cap history tail

        # Confidence: more evidence → higher confidence
        confidence = min(1.0, 0.3 + (ev * 0.10))
        row.confidence = Decimal(str(round(confidence, 4)))

        # Award-ready recomputation
        has_fresh_quote = bool(
            row.last_quote_date and (_today() - row.last_quote_date).days <= 180
            and row.last_quote_price is not None
        )
        award_ready = (
            has_fresh_quote
            and ev >= 2
            and int(row.po_win_count or 0) >= 1
            and float(row.match_score or 0) >= 0.70
        )
        row.award_ready = award_ready
        row.rfq_first_recommended = not award_ready
        row.last_updated_at = datetime.now(timezone.utc)
        db.flush()
        return row

    # ── Helpers ──────────────────────────────────────────────────────────

    def _confidence_from(
        self,
        match_score: float,
        evidence_count: int,
        trust_tier: str,
    ) -> float:
        base = match_score * 0.6
        if evidence_count >= 3:
            base += 0.20
        elif evidence_count >= 1:
            base += 0.10
        if trust_tier in ("PLATINUM", "GOLD"):
            base += 0.10
        elif trust_tier == "UNVERIFIED":
            base -= 0.10
        return max(0.0, min(1.0, base))

    def _upsert_index(
        self,
        db: Session,
        result: PartVendorMatchResult,
        existing: PartVendorIndex | None,
    ) -> PartVendorIndex:
        row = existing
        if row is None:
            row = PartVendorIndex(
                canonical_part_key=result.canonical_part_key,
                vendor_id=result.vendor_id,
            )
            db.add(row)

        row.match_type = result.match_type
        row.match_score = Decimal(str(round(result.match_score, 4)))
        row.confidence = Decimal(str(round(result.confidence, 4)))
        row.award_ready = bool(result.award_ready)
        row.rfq_first_recommended = bool(result.rfq_first_recommended)
        row.category_match_detail = result.category_match
        row.material_match_detail = result.material_match
        row.process_match_detail = result.process_match
        row.alias_match_detail = result.alias_match
        row.last_updated_at = datetime.now(timezone.utc)
        # evidence_count is preserved from historical_boost caller side
        if row.evidence_count is None:
            row.evidence_count = result.evidence_count
        return row

    def _load_active_vendor_pool(self, db: Session) -> list[dict[str, Any]]:
        vendors = (
            db.query(Vendor)
            .filter(
                Vendor.is_active.is_(True),
                Vendor.deleted_at.is_(None),
                Vendor.merged_into_vendor_id.is_(None),
            )
            .all()
        )
        pool: list[dict[str, Any]] = []
        for v in vendors:
            caps = (
                db.query(VendorCapability)
                .filter(
                    VendorCapability.vendor_id == v.id,
                    VendorCapability.is_active.is_(True),
                )
                .all()
            )
            pool.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "country": v.country,
                    "region": v.region,
                    "primary_category_tag": v.primary_category_tag,
                    "secondary_category_tags": v.secondary_category_tags or [],
                    "trust_tier": v.trust_tier or "UNVERIFIED",
                    "export_capable": bool(v.export_capable),
                    "reliability_score": _as_float(v.reliability_score, 0.5),
                    "avg_lead_time_days": _as_float(v.avg_lead_time_days, None) if v.avg_lead_time_days else None,
                    "capabilities": [
                        {"process": c.process, "material_family": c.material_family}
                        for c in caps
                    ],
                }
            )
        return pool


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


part_vendor_matcher = PartVendorMatcher()
