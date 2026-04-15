"""
Phase 2B Batch 1C: multi-source offer reconciliation.

Behavior:
- reads Batch 1A/1B canonical SKU linkage via source_sku_link
- reads raw Phase 2A offers from pricing.sku_offers and price breaks from
  pricing.sku_offer_price_breaks
- groups offers by canonical_sku_id
- filters invalid/expired/not-quantity-valid offers
- prefers fresh offers within TTL window, but falls back to stale and marks them
- normalizes price into a common currency using FX service
- writes one consolidated canonical_offer_snapshot row per canonical_sku_id
- preserves raw offers and provenance
- does not implement scoring, recompute, tariffs, freight, or availability logic
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.canonical import CanonicalOfferSnapshot, SourceSKULink
from app.models.enrichment import EnrichmentRunLog, SKUOffer, SKUOfferPriceBreak
from app.schemas.canonical_offer_reconciliation import (
    CanonicalOfferReconciliationResult,
    ReconciledOfferCandidate,
)
from app.services.enrichment.offer_ingestion_service import offer_ingestion_service
from app.services.market_data.fx_service import fx_service

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _age_minutes(value: datetime | None, as_of: datetime) -> int | None:
    value = _to_utc(value)
    if value is None:
        return None
    delta = as_of - value
    return max(0, int(delta.total_seconds() // 60))


def _first_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


class OfferReconciliationService:
    DEFAULT_FRESH_TTL_DAYS = 7
    DEFAULT_CONFLICT_SPREAD_THRESHOLD = Decimal("1.5")
    DEFAULT_TARGET_CURRENCY = "USD"
    DEFAULT_NORMALIZED_UOM = "EA"

    def _get_or_create_run_log(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        as_of: datetime,
    ) -> EnrichmentRunLog:
        row = (
            db.query(EnrichmentRunLog)
            .filter(
                EnrichmentRunLog.stage == "phase2b_offer_reconciliation",
                EnrichmentRunLog.request_hash == f"{canonical_sku_id}:{as_of.isoformat()}",
            )
            .first()
        )
        if row is not None:
            row.started_at = row.started_at or _now()
            row.status = "started"
            return row

        row = EnrichmentRunLog(
            bom_id=None,
            bom_part_id=None,
            run_scope="canonical_sku",
            stage="phase2b_offer_reconciliation",
            provider="internal",
            status="started",
            idempotency_key=f"phase2b_offer_reconciliation:{canonical_sku_id}:{as_of.isoformat()}",
            request_hash=f"{canonical_sku_id}:{as_of.isoformat()}",
            attempt_count=1,
            source_system="platform-api",
            source_metadata={"canonical_sku_id": canonical_sku_id},
            started_at=_now(),
        )
        db.add(row)
        db.flush()
        return row

    @contextmanager
    def _run_scope(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        as_of: datetime,
    ):
        run_log = self._get_or_create_run_log(db, canonical_sku_id=canonical_sku_id, as_of=as_of)
        started = _now()
        try:
            yield run_log
            run_log.status = "success"
        except Exception as exc:
            run_log.status = "failed"
            run_log.error_message = str(exc)[:500]
            raise
        finally:
            completed = _now()
            run_log.completed_at = completed
            run_log.duration_ms = int((completed - started).total_seconds() * 1000)

    def _quantity_valid_price_break(
        self,
        db: Session,
        *,
        sku_offer_id: str,
        requested_quantity: Decimal,
    ) -> SKUOfferPriceBreak | None:
        resolved = offer_ingestion_service.resolve_best_price_break(
            db,
            sku_offer_id=sku_offer_id,
            quantity=requested_quantity,
        )
        if resolved is None:
            return None

        rows = (
            db.query(SKUOfferPriceBreak)
            .filter(SKUOfferPriceBreak.sku_offer_id == sku_offer_id)
            .all()
        )
        for row in rows:
            if (
                _as_decimal(row.break_qty) == _as_decimal(resolved.break_qty)
                and _as_decimal(row.unit_price) == _as_decimal(resolved.unit_price)
                and (row.currency or "").upper() == (resolved.currency or "").upper()
            ):
                return row
        return None

    def _normalize_currency(
        self,
        db: Session,
        *,
        amount: Decimal,
        currency: str,
        target_currency: str,
    ) -> Decimal:
        base = (currency or target_currency or self.DEFAULT_TARGET_CURRENCY).upper()
        quote = (target_currency or self.DEFAULT_TARGET_CURRENCY).upper()
        if base == quote:
            return amount
        rate = fx_service.get_rate(
            db,
            base_currency=base,
            quote_currency=quote,
        )
        return (amount * rate).quantize(Decimal("0.00000001"))

    def _normalize_uom(
        self,
        *,
        offer_uom: str | None,
        source_metadata: dict[str, Any] | None,
    ) -> str:
        metadata = source_metadata or {}
        normalized = metadata.get("normalized_uom")
        if normalized:
            return str(normalized)
        if offer_uom:
            return str(offer_uom).upper()
        return self.DEFAULT_NORMALIZED_UOM

    def _is_offer_expired(self, offer: SKUOffer, as_of: datetime) -> bool:
        valid_to = _to_utc(offer.valid_to)
        return valid_to is not None and valid_to <= as_of

    def _candidate_from_offer(
        self,
        db: Session,
        *,
        link: SourceSKULink,
        offer: SKUOffer,
        requested_quantity: Decimal,
        as_of: datetime,
        fresh_cutoff: datetime,
        target_currency: str,
    ) -> ReconciledOfferCandidate | None:
        price_break = self._quantity_valid_price_break(
            db,
            sku_offer_id=offer.id,
            requested_quantity=requested_quantity,
        )
        if price_break is None:
            return None

        unit_price = _as_decimal(price_break.unit_price)
        if unit_price <= 0:
            return None

        if self._is_offer_expired(offer, as_of):
            return None

        observed_at = _to_utc(offer.observed_at)
        freshness_status = str(offer.freshness_status or "UNKNOWN").upper()
        is_stale = False
        if observed_at is not None and observed_at < fresh_cutoff:
            is_stale = True
        if freshness_status == "STALE":
            is_stale = True

        normalized_price = self._normalize_currency(
            db,
            amount=unit_price,
            currency=price_break.currency or offer.currency or target_currency,
            target_currency=target_currency,
        )
        normalized_uom = self._normalize_uom(
            offer_uom=offer.uom,
            source_metadata=offer.source_metadata,
        )

        external_offer_id = offer.source_record_id
        source_system = offer.source_system or link.source_system or "unknown"

        return ReconciledOfferCandidate(
            canonical_sku_id=link.canonical_sku_id,
            source_sku_link_id=link.id,
            sku_offer_id=offer.id,
            external_offer_id=external_offer_id,
            source_system=source_system,
            vendor_id=offer.vendor_id,
            currency=(price_break.currency or offer.currency or target_currency).upper(),
            normalized_currency=target_currency.upper(),
            uom=offer.uom,
            normalized_uom=normalized_uom,
            unit_price=unit_price,
            normalized_unit_price=normalized_price,
            break_qty=_as_decimal(price_break.break_qty) if price_break.break_qty is not None else None,
            valid_from=_to_utc(offer.valid_from),
            valid_to=_to_utc(offer.valid_to),
            observed_at=observed_at,
            freshness_status=freshness_status,
            is_stale=is_stale,
            source_metadata={
                "offer_source_metadata": offer.source_metadata or {},
                "price_break_source_metadata": price_break.source_metadata or {},
            },
        )

    def _collect_candidates(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        requested_quantity: Decimal,
        as_of: datetime,
        fresh_cutoff: datetime,
        target_currency: str,
    ) -> list[ReconciledOfferCandidate]:
        links = (
            db.query(SourceSKULink)
            .filter(SourceSKULink.canonical_sku_id == canonical_sku_id)
            .order_by(SourceSKULink.updated_at.desc())
            .all()
        )

        candidates: list[ReconciledOfferCandidate] = []
        seen_offer_ids: set[str] = set()

        for link in links:
            mapping_id = _first_attr(link, "part_to_sku_mapping_id")
            if not mapping_id:
                continue

            offers = (
                db.query(SKUOffer)
                .filter(SKUOffer.part_to_sku_mapping_id == mapping_id)
                .order_by(SKUOffer.observed_at.desc(), SKUOffer.updated_at.desc())
                .all()
            )

            for offer in offers:
                if offer.id in seen_offer_ids:
                    continue
                candidate = self._candidate_from_offer(
                    db,
                    link=link,
                    offer=offer,
                    requested_quantity=requested_quantity,
                    as_of=as_of,
                    fresh_cutoff=fresh_cutoff,
                    target_currency=target_currency,
                )
                if candidate is None:
                    continue
                seen_offer_ids.add(offer.id)
                candidates.append(candidate)

        return candidates

    def _select_best_candidates(
        self,
        candidates: list[ReconciledOfferCandidate],
    ) -> tuple[list[ReconciledOfferCandidate], bool]:
        fresh = [c for c in candidates if not c.is_stale]
        if fresh:
            return fresh, False
        return candidates, True

    def _compute_price_spread(
        self,
        candidates: list[ReconciledOfferCandidate],
    ) -> Decimal | None:
        if not candidates:
            return None
        prices = [c.normalized_unit_price for c in candidates if c.normalized_unit_price > 0]
        if not prices:
            return None
        low = min(prices)
        high = max(prices)
        if low <= 0:
            return None
        return (high / low).quantize(Decimal("0.0001"))

    def _latest_valid_through(
        self,
        candidates: list[ReconciledOfferCandidate],
    ) -> datetime | None:
        values = [c.valid_to for c in candidates if c.valid_to is not None]
        return max(values) if values else None

    def _newest_offer_age_minutes(
        self,
        candidates: list[ReconciledOfferCandidate],
        as_of: datetime,
    ) -> int | None:
        newest = None
        for candidate in candidates:
            observed_at = _to_utc(candidate.observed_at)
            if observed_at is None:
                continue
            newest = observed_at if newest is None else max(newest, observed_at)
        return _age_minutes(newest, as_of)

    def _load_existing_snapshot(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
    ) -> CanonicalOfferSnapshot | None:
        return (
            db.query(CanonicalOfferSnapshot)
            .filter(CanonicalOfferSnapshot.canonical_sku_id == canonical_sku_id)
            .order_by(CanonicalOfferSnapshot.updated_at.desc(), CanonicalOfferSnapshot.created_at.desc())
            .first()
        )

    def _assign_if_present(self, obj: Any, field_name: str, value: Any) -> None:
        if hasattr(obj, field_name):
            setattr(obj, field_name, value)

    def _upsert_snapshot(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        as_of: datetime,
        selected_candidates: list[ReconciledOfferCandidate],
        stale_fallback_used: bool,
        conflict_threshold: Decimal,
    ) -> CanonicalOfferReconciliationResult:
        selected_candidates = sorted(
            selected_candidates,
            key=lambda c: (
                c.normalized_unit_price,
                c.is_stale,
                c.source_system,
                c.external_offer_id or "",
            ),
        )

        best = selected_candidates[0] if selected_candidates else None
        offer_count = len(selected_candidates)
        price_spread = self._compute_price_spread(selected_candidates)
        has_conflict = bool(
            price_spread is not None and price_spread > conflict_threshold
        )
        freshness_minutes = self._newest_offer_age_minutes(selected_candidates, as_of)
        valid_through = self._latest_valid_through(selected_candidates)
        is_stale = stale_fallback_used

        snapshot = self._load_existing_snapshot(db, canonical_sku_id=canonical_sku_id)
        if snapshot is None:
            snapshot = CanonicalOfferSnapshot(canonical_sku_id=canonical_sku_id)
            db.add(snapshot)
            db.flush()

        # Backward-compatible mapping:
        # - explicit Batch 1C columns, if they exist
        # - otherwise map into the generic Batch 1A snapshot columns + evidence_metadata
        if best is not None:
            self._assign_if_present(snapshot, "source_sku_link_id", best.source_sku_link_id)
            self._assign_if_present(snapshot, "source_offer_id", best.sku_offer_id)
            self._assign_if_present(snapshot, "vendor_id", best.vendor_id)
            self._assign_if_present(snapshot, "currency", best.normalized_currency)
            self._assign_if_present(snapshot, "unit_price", best.normalized_unit_price)
            self._assign_if_present(snapshot, "offer_status", "ACTIVE")
            self._assign_if_present(snapshot, "freshness_status", "STALE" if is_stale else "FRESH")
            self._assign_if_present(snapshot, "observed_at", as_of)
            self._assign_if_present(snapshot, "valid_from", as_of)
            self._assign_if_present(snapshot, "valid_to", valid_through)
            self._assign_if_present(snapshot, "consolidation_method", "phase2b_offer_reconciliation")
            self._assign_if_present(snapshot, "moq", None)
            self._assign_if_present(snapshot, "spq", None)

            # Optional explicit columns if the Batch 1A model was later extended.
            self._assign_if_present(snapshot, "as_of", as_of)
            self._assign_if_present(snapshot, "best_price", best.normalized_unit_price)
            self._assign_if_present(snapshot, "best_currency", best.normalized_currency)
            self._assign_if_present(snapshot, "best_source_system", best.source_system)
            self._assign_if_present(snapshot, "best_external_offer_id", best.external_offer_id)
            self._assign_if_present(snapshot, "price_spread", price_spread)
            self._assign_if_present(snapshot, "offer_count", offer_count)
            self._assign_if_present(snapshot, "freshness_minutes", freshness_minutes)
            self._assign_if_present(snapshot, "valid_through", valid_through)

        evidence_metadata = dict(_first_attr(snapshot, "evidence_metadata", default={}) or {})
        evidence_metadata.update(
            {
                "phase": "2B-Batch-1C",
                "as_of": as_of.isoformat(),
                "best_price": str(best.normalized_unit_price) if best is not None else None,
                "best_currency": best.normalized_currency if best is not None else None,
                "best_source_system": best.source_system if best is not None else None,
                "best_external_offer_id": best.external_offer_id if best is not None else None,
                "best_sku_offer_id": best.sku_offer_id if best is not None else None,
                "best_source_sku_link_id": best.source_sku_link_id if best is not None else None,
                "price_spread": str(price_spread) if price_spread is not None else None,
                "offer_count": offer_count,
                "freshness_minutes": freshness_minutes,
                "valid_through": valid_through.isoformat() if valid_through is not None else None,
                "is_stale": is_stale,
                "has_conflict": has_conflict,
                "stale_fallback_used": stale_fallback_used,
                "conflict_threshold": str(conflict_threshold),
                "target_currency": best.normalized_currency if best is not None else None,
                "normalized_uom": best.normalized_uom if best is not None else None,
                "candidate_offers": [asdict(c) for c in selected_candidates],
            }
        )
        self._assign_if_present(snapshot, "evidence_metadata", evidence_metadata)
        self._assign_if_present(snapshot, "updated_at", _now())

        db.flush()

        return CanonicalOfferReconciliationResult(
            canonical_sku_id=canonical_sku_id,
            as_of=as_of,
            best_price=best.normalized_unit_price if best is not None else None,
            best_currency=best.normalized_currency if best is not None else None,
            best_source_system=best.source_system if best is not None else None,
            best_external_offer_id=best.external_offer_id if best is not None else None,
            best_sku_offer_id=best.sku_offer_id if best is not None else None,
            price_spread=price_spread,
            offer_count=offer_count,
            freshness_minutes=freshness_minutes,
            valid_through=valid_through,
            is_stale=is_stale,
            has_conflict=has_conflict,
            snapshot_id=snapshot.id,
            candidate_offer_ids=[c.sku_offer_id for c in selected_candidates],
            notes=["stale_fallback_used"] if stale_fallback_used else [],
        )

    def reconcile_for_canonical_sku(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        requested_quantity: Decimal,
        as_of: datetime | None = None,
        target_currency: str = DEFAULT_TARGET_CURRENCY,
        fresh_ttl_days: int = DEFAULT_FRESH_TTL_DAYS,
        conflict_spread_threshold: Decimal = DEFAULT_CONFLICT_SPREAD_THRESHOLD,
    ) -> CanonicalOfferReconciliationResult | None:
        as_of = _to_utc(as_of) or _now()
        fresh_cutoff = as_of - timedelta(days=fresh_ttl_days)

        with self._run_scope(db, canonical_sku_id=canonical_sku_id, as_of=as_of) as run_log:
            candidates = self._collect_candidates(
                db,
                canonical_sku_id=canonical_sku_id,
                requested_quantity=requested_quantity,
                as_of=as_of,
                fresh_cutoff=fresh_cutoff,
                target_currency=target_currency,
            )
            if not candidates:
                run_log.records_written = 0
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "canonical_sku_id": canonical_sku_id,
                    "requested_quantity": str(requested_quantity),
                    "reason": "no_valid_offers",
                }
                return None

            selected_candidates, stale_fallback_used = self._select_best_candidates(candidates)
            result = self._upsert_snapshot(
                db,
                canonical_sku_id=canonical_sku_id,
                as_of=as_of,
                selected_candidates=selected_candidates,
                stale_fallback_used=stale_fallback_used,
                conflict_threshold=conflict_spread_threshold,
            )

            run_log.records_written = 1 if result.snapshot_id else 0
            run_log.records_skipped = max(0, len(candidates) - result.offer_count)
            run_log.source_metadata = {
                "canonical_sku_id": canonical_sku_id,
                "requested_quantity": str(requested_quantity),
                "candidate_offer_count": len(candidates),
                "selected_offer_count": result.offer_count,
                "has_conflict": result.has_conflict,
                "is_stale": result.is_stale,
                "best_sku_offer_id": result.best_sku_offer_id,
            }
            return result


offer_reconciliation_service = OfferReconciliationService()