"""
Phase 2B Batch 1D: multi-source availability reconciliation.

Behavior:
- reads Batch 1A/1B canonical SKU linkage via source_sku_link
- reads raw Phase 2A availability snapshots from market.sku_availability_snapshots
- groups snapshots by canonical_sku_id
- picks latest snapshot per source system
- ignores clearly invalid data (negative qty, corrupt fields)
- applies conservative merge logic
- preserves conflicts via downgraded status + multiple source systems
- writes one consolidated canonical_availability_snapshot row per canonical_sku_id
- does not implement scoring, recompute, telemetry, or availability ingestion changes
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.canonical import CanonicalAvailabilitySnapshot, SourceSKULink
from app.models.enrichment import EnrichmentRunLog, SKUOffer
from app.models.enrichment import SKUAvailabilitySnapshot
from app.schemas.canonical_availability_reconciliation import (
    CanonicalAvailabilityReconciliationResult,
    ReconciledAvailabilityCandidate,
)

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


class AvailabilityReconciliationService:
    STAGE = "phase2b_availability_reconciliation"
    MAX_REASONABLE_AVAILABLE_QTY = Decimal("1000000")

    STATUS_IN_STOCK = "IN_STOCK"
    STATUS_LIMITED_STOCK = "LIMITED_STOCK"
    STATUS_BACKORDER = "BACKORDER"
    STATUS_OUT_OF_STOCK = "OUT_OF_STOCK"
    STATUS_UNKNOWN = "UNKNOWN"

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
                EnrichmentRunLog.stage == self.STAGE,
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
            stage=self.STAGE,
            provider="internal",
            status="started",
            idempotency_key=f"{self.STAGE}:{canonical_sku_id}:{as_of.isoformat()}",
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

    def _normalize_status(self, raw_status: str | None) -> str:
        value = str(raw_status or "").strip().upper()
        aliases = {
            "IN_STOCK": self.STATUS_IN_STOCK,
            "INSTOCK": self.STATUS_IN_STOCK,
            "AVAILABLE": self.STATUS_IN_STOCK,
            "LIMITED_STOCK": self.STATUS_LIMITED_STOCK,
            "LIMITED": self.STATUS_LIMITED_STOCK,
            "LOW_STOCK": self.STATUS_LIMITED_STOCK,
            "BACKORDER": self.STATUS_BACKORDER,
            "BACK_ORDER": self.STATUS_BACKORDER,
            "OOS": self.STATUS_OUT_OF_STOCK,
            "OUT_OF_STOCK": self.STATUS_OUT_OF_STOCK,
            "OUT": self.STATUS_OUT_OF_STOCK,
            "UNKNOWN": self.STATUS_UNKNOWN,
        }
        return aliases.get(value, self.STATUS_UNKNOWN)

    def _is_invalid_snapshot(self, snapshot: SKUAvailabilitySnapshot) -> bool:
        qty_fields = [
            snapshot.available_qty,
            snapshot.on_order_qty,
            snapshot.allocated_qty,
            snapshot.backorder_qty,
            snapshot.moq,
        ]
        for value in qty_fields:
            if value is not None and _as_decimal(value) < 0:
                return True

        if snapshot.factory_lead_time_days is not None and _as_decimal(snapshot.factory_lead_time_days) < 0:
            return True

        if snapshot.snapshot_at is None:
            return True

        return False

    def _candidate_from_snapshot(
        self,
        *,
        canonical_sku_id: str,
        link: SourceSKULink,
        offer: SKUOffer,
        snapshot: SKUAvailabilitySnapshot,
    ) -> ReconciledAvailabilityCandidate | None:
        if self._is_invalid_snapshot(snapshot):
            return None

        return ReconciledAvailabilityCandidate(
            canonical_sku_id=canonical_sku_id,
            source_sku_link_id=link.id,
            sku_offer_id=offer.id,
            availability_snapshot_id=snapshot.id,
            external_snapshot_id=snapshot.source_record_id,
            source_system=snapshot.source_system or offer.source_system or link.source_system or "unknown",
            availability_status=self._normalize_status(snapshot.availability_status),
            available_qty=_as_decimal(snapshot.available_qty) if snapshot.available_qty is not None else None,
            on_order_qty=_as_decimal(snapshot.on_order_qty) if snapshot.on_order_qty is not None else None,
            allocated_qty=_as_decimal(snapshot.allocated_qty) if snapshot.allocated_qty is not None else None,
            backorder_qty=_as_decimal(snapshot.backorder_qty) if snapshot.backorder_qty is not None else None,
            lead_time_days=_as_decimal(snapshot.factory_lead_time_days) if snapshot.factory_lead_time_days is not None else None,
            inventory_location=snapshot.inventory_location,
            snapshot_at=_to_utc(snapshot.snapshot_at),
            freshness_status=str(snapshot.freshness_status or "UNKNOWN").upper(),
            source_metadata={
                "availability_source_metadata": snapshot.source_metadata or {},
                "offer_source_metadata": offer.source_metadata or {},
            },
        )

    def _collect_latest_per_source(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
    ) -> list[ReconciledAvailabilityCandidate]:
        links = (
            db.query(SourceSKULink)
            .filter(SourceSKULink.canonical_sku_id == canonical_sku_id)
            .order_by(SourceSKULink.updated_at.desc())
            .all()
        )

        latest_by_source: dict[str, ReconciledAvailabilityCandidate] = {}

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
                snapshot = (
                    db.query(SKUAvailabilitySnapshot)
                    .filter(SKUAvailabilitySnapshot.sku_offer_id == offer.id)
                    .order_by(
                        SKUAvailabilitySnapshot.snapshot_at.desc(),
                        SKUAvailabilitySnapshot.created_at.desc(),
                    )
                    .first()
                )
                if snapshot is None:
                    continue

                candidate = self._candidate_from_snapshot(
                    canonical_sku_id=canonical_sku_id,
                    link=link,
                    offer=offer,
                    snapshot=snapshot,
                )
                if candidate is None:
                    continue

                existing = latest_by_source.get(candidate.source_system)
                if existing is None:
                    latest_by_source[candidate.source_system] = candidate
                    continue

                existing_ts = _to_utc(existing.snapshot_at) or datetime.min.replace(tzinfo=timezone.utc)
                candidate_ts = _to_utc(candidate.snapshot_at) or datetime.min.replace(tzinfo=timezone.utc)
                if candidate_ts > existing_ts:
                    latest_by_source[candidate.source_system] = candidate

        return list(latest_by_source.values())

    def _sum_available_qty(
        self,
        candidates: list[ReconciledAvailabilityCandidate],
    ) -> tuple[Decimal | None, bool]:
        total = Decimal("0")
        any_qty = False

        for candidate in candidates:
            if candidate.availability_status not in {self.STATUS_IN_STOCK, self.STATUS_LIMITED_STOCK}:
                continue
            if candidate.available_qty is None:
                continue
            if candidate.available_qty < 0:
                continue

            total += candidate.available_qty
            any_qty = True

        if not any_qty:
            return None, False

        if total > self.MAX_REASONABLE_AVAILABLE_QTY:
            return self.MAX_REASONABLE_AVAILABLE_QTY, True

        return total, False

    def _best_lead_time(
        self,
        candidates: list[ReconciledAvailabilityCandidate],
    ) -> Decimal | None:
        values = [
            candidate.lead_time_days
            for candidate in candidates
            if candidate.lead_time_days is not None and candidate.lead_time_days >= 0
        ]
        return min(values) if values else None

    def _newest_snapshot_age_minutes(
        self,
        candidates: list[ReconciledAvailabilityCandidate],
        as_of: datetime,
    ) -> int | None:
        newest = None
        for candidate in candidates:
            snapshot_at = _to_utc(candidate.snapshot_at)
            if snapshot_at is None:
                continue
            newest = snapshot_at if newest is None else max(newest, snapshot_at)
        return _age_minutes(newest, as_of)

    def _resolve_status(
        self,
        candidates: list[ReconciledAvailabilityCandidate],
    ) -> tuple[str, bool]:
        if not candidates:
            return self.STATUS_UNKNOWN, False

        statuses = {candidate.availability_status for candidate in candidates}

        # explicit conflicting signal downgrade
        if self.STATUS_IN_STOCK in statuses and self.STATUS_OUT_OF_STOCK in statuses:
            return self.STATUS_LIMITED_STOCK, True

        if self.STATUS_IN_STOCK in statuses:
            return self.STATUS_IN_STOCK, False

        if self.STATUS_LIMITED_STOCK in statuses:
            return self.STATUS_LIMITED_STOCK, False

        if self.STATUS_BACKORDER in statuses:
            return self.STATUS_BACKORDER, False

        if statuses and statuses == {self.STATUS_OUT_OF_STOCK}:
            return self.STATUS_OUT_OF_STOCK, False

        return self.STATUS_UNKNOWN, False

    def _load_existing_snapshot(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
    ) -> CanonicalAvailabilitySnapshot | None:
        return (
            db.query(CanonicalAvailabilitySnapshot)
            .filter(CanonicalAvailabilitySnapshot.canonical_sku_id == canonical_sku_id)
            .order_by(CanonicalAvailabilitySnapshot.updated_at.desc(), CanonicalAvailabilitySnapshot.created_at.desc())
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
        candidates: list[ReconciledAvailabilityCandidate],
    ) -> CanonicalAvailabilityReconciliationResult:
        availability_status, has_conflict = self._resolve_status(candidates)
        available_qty, qty_capped = self._sum_available_qty(candidates)
        lead_time_days = None
        if availability_status not in {self.STATUS_IN_STOCK, self.STATUS_LIMITED_STOCK}:
            lead_time_days = self._best_lead_time(candidates)

        freshness_minutes = self._newest_snapshot_age_minutes(candidates, as_of)
        source_systems = sorted({candidate.source_system for candidate in candidates if candidate.source_system})

        snapshot = self._load_existing_snapshot(db, canonical_sku_id=canonical_sku_id)
        if snapshot is None:
            snapshot = CanonicalAvailabilitySnapshot(canonical_sku_id=canonical_sku_id)
            db.add(snapshot)
            db.flush()

        primary = None
        if candidates:
            primary = sorted(
                candidates,
                key=lambda c: (
                    _to_utc(c.snapshot_at) or datetime.min.replace(tzinfo=timezone.utc),
                    c.source_system,
                ),
                reverse=True,
            )[0]

        if primary is not None:
            self._assign_if_present(snapshot, "source_sku_link_id", primary.source_sku_link_id)
            self._assign_if_present(snapshot, "source_offer_id", primary.sku_offer_id)
            self._assign_if_present(snapshot, "source_availability_snapshot_id", primary.availability_snapshot_id)
            self._assign_if_present(snapshot, "availability_status", availability_status)
            self._assign_if_present(snapshot, "available_qty", available_qty)
            self._assign_if_present(snapshot, "factory_lead_time_days", lead_time_days)
            self._assign_if_present(snapshot, "freshness_status", "STALE" if freshness_minutes and freshness_minutes > 0 else "FRESH")
            self._assign_if_present(snapshot, "snapshot_at", as_of)
            self._assign_if_present(snapshot, "consolidation_method", "phase2b_availability_reconciliation")
            self._assign_if_present(snapshot, "inventory_location", None)

            # Optional explicit columns if the Batch 1A model was later extended.
            self._assign_if_present(snapshot, "as_of", as_of)
            self._assign_if_present(snapshot, "lead_time_days", lead_time_days)
            self._assign_if_present(snapshot, "source_systems", source_systems)
            self._assign_if_present(snapshot, "freshness_minutes", freshness_minutes)

        evidence_metadata = dict(_first_attr(snapshot, "evidence_metadata", default={}) or {})
        evidence_metadata.update(
            {
                "phase": "2B-Batch-1D",
                "as_of": as_of.isoformat(),
                "availability_status": availability_status,
                "available_qty": str(available_qty) if available_qty is not None else None,
                "lead_time_days": str(lead_time_days) if lead_time_days is not None else None,
                "source_systems": source_systems,
                "freshness_minutes": freshness_minutes,
                "has_conflict": has_conflict,
                "qty_capped": qty_capped,
                "candidate_snapshots": [
                    {
                        "availability_snapshot_id": c.availability_snapshot_id,
                        "sku_offer_id": c.sku_offer_id,
                        "external_snapshot_id": c.external_snapshot_id,
                        "source_system": c.source_system,
                        "availability_status": c.availability_status,
                        "available_qty": str(c.available_qty) if c.available_qty is not None else None,
                        "lead_time_days": str(c.lead_time_days) if c.lead_time_days is not None else None,
                        "snapshot_at": c.snapshot_at.isoformat() if c.snapshot_at else None,
                    }
                    for c in candidates
                ],
            }
        )
        self._assign_if_present(snapshot, "evidence_metadata", evidence_metadata)
        self._assign_if_present(snapshot, "updated_at", _now())

        db.flush()

        notes: list[str] = []
        if has_conflict:
            notes.append("conflict_downgraded_to_limited_stock")
        if qty_capped:
            notes.append("available_qty_capped")
        if availability_status == self.STATUS_UNKNOWN:
            notes.append("no_reliable_data")

        return CanonicalAvailabilityReconciliationResult(
            canonical_sku_id=canonical_sku_id,
            as_of=as_of,
            availability_status=availability_status,
            available_qty=available_qty,
            lead_time_days=lead_time_days,
            source_systems=source_systems,
            freshness_minutes=freshness_minutes,
            snapshot_id=snapshot.id,
            candidate_snapshot_ids=[c.availability_snapshot_id for c in candidates],
            has_conflict=has_conflict,
            notes=notes,
        )

    def reconcile_for_canonical_sku(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        as_of: datetime | None = None,
    ) -> CanonicalAvailabilityReconciliationResult:
        as_of = _to_utc(as_of) or _now()

        with self._run_scope(db, canonical_sku_id=canonical_sku_id, as_of=as_of) as run_log:
            candidates = self._collect_latest_per_source(
                db,
                canonical_sku_id=canonical_sku_id,
            )

            result = self._upsert_snapshot(
                db,
                canonical_sku_id=canonical_sku_id,
                as_of=as_of,
                candidates=candidates,
            )

            run_log.records_written = 1 if result.snapshot_id else 0
            run_log.records_skipped = 0
            run_log.source_metadata = {
                "canonical_sku_id": canonical_sku_id,
                "candidate_snapshot_count": len(candidates),
                "availability_status": result.availability_status,
                "has_conflict": result.has_conflict,
                "source_systems": result.source_systems,
            }
            return result


availability_reconciliation_service = AvailabilityReconciliationService()