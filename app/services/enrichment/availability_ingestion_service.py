"""
Phase 2A Batch 2: availability ingestion pipeline.

Behavior:
- checks latest availability snapshot first
- refreshes only if stale or missing
- calls connector abstraction for availability
- stores append-only market.sku_availability_snapshots
- computes internal feasibility tag in source_metadata:
  - feasible_now
  - feasible_by_date
  - unknown
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.distributor_connector import (
    NullProductDataConnector,
    ProductDataConnector,
)
from app.models.bom import BOMPart
from app.models.enrichment import (
    EnrichmentRunLog,
    PartToSkuMapping,
    SKUAvailabilitySnapshot,
    SKUOffer,
)
from app.schemas.enrichment import AvailabilityDTO, ProductSearchCandidate


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _mapping_candidate(mapping: PartToSkuMapping) -> ProductSearchCandidate:
    metadata = mapping.source_metadata or {}
    return ProductSearchCandidate(
        vendor_id=mapping.vendor_id,
        vendor_sku=mapping.vendor_sku,
        manufacturer=mapping.manufacturer,
        mpn=mapping.mpn,
        normalized_mpn=mapping.normalized_mpn,
        canonical_part_key=mapping.canonical_part_key,
        match_method=mapping.match_method,
        match_score=_as_decimal(mapping.confidence),
        mapping_status=str(metadata.get("mapping_status") or "resolved"),
        source_system=mapping.source_system,
        source_record_id=mapping.source_record_id,
        source_metadata=metadata,
        is_preferred=bool(mapping.is_preferred),
    )


class AvailabilityIngestionService:
    STAGE = "availability_ingestion"
    DEFAULT_TTL_SECONDS = 900

    def _get_or_create_run_log(
        self,
        db: Session,
        *,
        bom_part: BOMPart | None,
        provider: str,
        request_hash: str,
        offer_id: str,
    ) -> EnrichmentRunLog:
        idempotency_key = f"{self.STAGE}:{offer_id}:{request_hash}"
        existing = (
            db.query(EnrichmentRunLog)
            .filter(EnrichmentRunLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing

        log = EnrichmentRunLog(
            bom_id=bom_part.bom_id if bom_part else None,
            bom_part_id=bom_part.id if bom_part else None,
            run_scope="bom_line" if bom_part else "batch",
            stage=self.STAGE,
            provider=provider,
            status="started",
            idempotency_key=idempotency_key,
            attempt_count=1,
            request_hash=request_hash,
            source_system="platform-api",
            source_metadata={},
            started_at=_now(),
        )
        db.add(log)
        db.flush()
        return log

    def _latest_snapshot(self, db: Session, sku_offer_id: str) -> SKUAvailabilitySnapshot | None:
        return (
            db.query(SKUAvailabilitySnapshot)
            .filter(SKUAvailabilitySnapshot.sku_offer_id == sku_offer_id)
            .order_by(SKUAvailabilitySnapshot.snapshot_at.desc(), SKUAvailabilitySnapshot.created_at.desc())
            .first()
        )

    def _snapshot_ttl_seconds(self, snapshot: SKUAvailabilitySnapshot | None, offer: SKUOffer) -> int:
        if snapshot:
            ttl = (snapshot.source_metadata or {}).get("ttl_seconds")
            if ttl is not None:
                try:
                    return int(ttl)
                except Exception:
                    pass
        ttl = (offer.source_metadata or {}).get("ttl_seconds")
        if ttl is not None:
            try:
                return int(ttl)
            except Exception:
                pass
        return self.DEFAULT_TTL_SECONDS

    def _is_stale(self, snapshot: SKUAvailabilitySnapshot | None, offer: SKUOffer) -> bool:
        if snapshot is None:
            return True
        age_seconds = (_now() - snapshot.snapshot_at).total_seconds()
        return age_seconds >= self._snapshot_ttl_seconds(snapshot, offer)

    def _feasibility_tag(
        self,
        *,
        bom_quantity: Decimal | None,
        need_by_date: date | None,
        available_qty: Decimal | None,
        lead_time_days: Decimal | None,
    ) -> str:
        if bom_quantity is None or available_qty is None:
            return "unknown"

        if available_qty >= bom_quantity:
            return "feasible_now"

        if need_by_date is None or lead_time_days is None:
            return "unknown"

        candidate_date = _now().date() + timedelta(days=int(lead_time_days))
        if candidate_date <= need_by_date:
            return "feasible_by_date"

        return "unknown"

    def _insert_snapshot(
        self,
        db: Session,
        *,
        sku_offer: SKUOffer,
        availability: AvailabilityDTO,
        bom_quantity: Decimal | None,
        need_by_date: date | None,
    ) -> SKUAvailabilitySnapshot:
        snapshot_at = availability.snapshot_at or _now()
        ttl_seconds = availability.ttl_seconds or self.DEFAULT_TTL_SECONDS

        source_payload = {
            "sku_offer_id": sku_offer.id,
            "vendor_sku": (sku_offer.source_metadata or {}).get("vendor_sku"),
            "status": availability.availability_status,
            "location": availability.inventory_location,
            "snapshot_at": snapshot_at.isoformat(),
            "available_qty": str(availability.available_qty) if availability.available_qty is not None else None,
        }
        source_hash = _hash_payload(source_payload)

        row = (
            db.query(SKUAvailabilitySnapshot)
            .filter(
                (SKUAvailabilitySnapshot.source_record_hash == source_hash) |
                (
                    (SKUAvailabilitySnapshot.sku_offer_id == sku_offer.id) &
                    (SKUAvailabilitySnapshot.inventory_location == availability.inventory_location) &
                    (SKUAvailabilitySnapshot.snapshot_at == snapshot_at)
                )
            )
            .first()
        )

        metadata = dict(availability.source_metadata or {})
        metadata.update(
            {
                "ttl_seconds": ttl_seconds,
                "feasibility_tag": self._feasibility_tag(
                    bom_quantity=bom_quantity,
                    need_by_date=need_by_date,
                    available_qty=availability.available_qty,
                    lead_time_days=availability.factory_lead_time_days,
                ),
            }
        )

        if row is None:
            row = SKUAvailabilitySnapshot(
                sku_offer_id=sku_offer.id,
                availability_status=availability.availability_status,
                available_qty=availability.available_qty,
                on_order_qty=availability.on_order_qty,
                allocated_qty=availability.allocated_qty,
                backorder_qty=availability.backorder_qty,
                moq=availability.moq,
                factory_lead_time_days=availability.factory_lead_time_days,
                inventory_location=availability.inventory_location,
                freshness_status="FRESH",
                snapshot_at=snapshot_at,
                source_system=availability.source_system,
                source_record_id=availability.source_record_id,
                source_record_hash=source_hash,
                source_metadata=metadata,
            )
            db.add(row)
        else:
            row.availability_status = availability.availability_status
            row.available_qty = availability.available_qty
            row.on_order_qty = availability.on_order_qty
            row.allocated_qty = availability.allocated_qty
            row.backorder_qty = availability.backorder_qty
            row.moq = availability.moq
            row.factory_lead_time_days = availability.factory_lead_time_days
            row.inventory_location = availability.inventory_location
            row.freshness_status = "FRESH"
            row.snapshot_at = snapshot_at
            row.source_system = availability.source_system
            row.source_record_id = availability.source_record_id
            row.source_record_hash = source_hash
            row.source_metadata = metadata

        db.flush()
        return row

    def get_or_refresh_latest(
        self,
        db: Session,
        *,
        sku_offer: SKUOffer,
        mapping: PartToSkuMapping,
        connector: ProductDataConnector | None = None,
        bom_part: BOMPart | None = None,
        need_by_date: date | None = None,
    ) -> list[SKUAvailabilitySnapshot]:
        connector = connector or NullProductDataConnector()
        latest = self._latest_snapshot(db, sku_offer.id)
        if not self._is_stale(latest, sku_offer):
            return [latest]

        candidate = _mapping_candidate(mapping)
        request_hash = _hash_payload(
            {
                "offer_id": sku_offer.id,
                "mapping_id": mapping.id,
                "vendor_sku": mapping.vendor_sku,
                "provider": connector.provider_name,
            }
        )
        run_log = self._get_or_create_run_log(
            db,
            bom_part=bom_part,
            provider=connector.provider_name,
            request_hash=request_hash,
            offer_id=sku_offer.id,
        )

        try:
            availabilities = connector.fetch_availability(candidate)
            qty = None
            if bom_part is not None:
                qty = _as_decimal(bom_part.quantity, "1")

            rows = [
                self._insert_snapshot(
                    db,
                    sku_offer=sku_offer,
                    availability=availability,
                    bom_quantity=qty,
                    need_by_date=need_by_date,
                )
                for availability in availabilities
            ]
            run_log.records_written = len(rows)
            run_log.records_skipped = 0
            run_log.status = "success"
            run_log.source_metadata = {"snapshot_count": len(rows)}
            run_log.completed_at = _now()
            run_log.duration_ms = int((run_log.completed_at - run_log.started_at).total_seconds() * 1000)
            return rows
        except Exception as exc:
            run_log.status = "failed"
            run_log.error_message = str(exc)[:500]
            run_log.completed_at = _now()
            run_log.duration_ms = int((run_log.completed_at - run_log.started_at).total_seconds() * 1000)
            raise


availability_ingestion_service = AvailabilityIngestionService()