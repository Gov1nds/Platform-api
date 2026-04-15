
"""
Phase 2B Batch 3: lane rate ingestion for broader lane/mode/service coverage.

Behavior:
- ingests lane bands append-only with effective-date awareness
- preserves historical windows by closing prior overlaps non-destructively
- updates lane scope registry metadata for covered lanes
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.enrichment import EnrichmentRunLog, LaneRateBand
from app.schemas.enrichment import LaneLookupContextDTO
from app.services.enrichment.lane_scope_service import lane_scope_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().upper()
    return value[:3] if value else None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class LaneRateIngestionService:
    STAGE = "lane_rate_ingestion"

    def _get_or_create_run_log(self, db: Session, *, provider: str, request_hash: str, scope_key: str) -> EnrichmentRunLog:
        idempotency_key = f"{self.STAGE}:{scope_key}:{request_hash}"
        existing = db.query(EnrichmentRunLog).filter(EnrichmentRunLog.idempotency_key == idempotency_key).first()
        if existing:
            return existing
        row = EnrichmentRunLog(
            run_scope="batch",
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
        db.add(row)
        db.flush()
        return row

    def _close_overlaps(
        self,
        db: Session,
        *,
        origin_country: str,
        destination_country: str,
        mode: str,
        service_level: str | None,
        origin_region: str | None,
        destination_region: str | None,
        min_weight_kg: Decimal | None,
        max_weight_kg: Decimal | None,
        effective_from: datetime,
        preserve_record_id: str | None,
    ) -> int:
        rows = (
            db.query(LaneRateBand)
            .filter(
                LaneRateBand.origin_country == origin_country,
                LaneRateBand.destination_country == destination_country,
                LaneRateBand.mode == mode,
                (LaneRateBand.service_level == service_level if service_level is not None else LaneRateBand.service_level.is_(None)),
                (LaneRateBand.origin_region == origin_region if origin_region is not None else LaneRateBand.origin_region.is_(None)),
                (LaneRateBand.destination_region == destination_region if destination_region is not None else LaneRateBand.destination_region.is_(None)),
                (LaneRateBand.min_weight_kg == min_weight_kg if min_weight_kg is not None else LaneRateBand.min_weight_kg.is_(None)),
                (LaneRateBand.max_weight_kg == max_weight_kg if max_weight_kg is not None else LaneRateBand.max_weight_kg.is_(None)),
                LaneRateBand.effective_from < effective_from,
                or_(LaneRateBand.effective_to.is_(None), LaneRateBand.effective_to > effective_from),
            )
            .all()
        )
        changed = 0
        for row in rows:
            if preserve_record_id and row.source_record_id == preserve_record_id:
                continue
            row.effective_to = effective_from
            row.updated_at = _now()
            changed += 1
        if changed:
            db.flush()
        return changed

    def ingest_lane_rate_band(
        self,
        db: Session,
        *,
        origin_country: str,
        destination_country: str,
        mode: str,
        service_level: str | None = None,
        origin_region: str | None = None,
        destination_region: str | None = None,
        min_weight_kg: Decimal | str | int | float | None = None,
        max_weight_kg: Decimal | str | int | float | None = None,
        min_volume_cbm: Decimal | str | int | float | None = None,
        max_volume_cbm: Decimal | str | int | float | None = None,
        currency: str = "USD",
        rate_type: str = "per_kg",
        rate_value: Decimal | str | int | float = Decimal("0"),
        min_charge: Decimal | str | int | float | None = None,
        transit_days_min: int | None = None,
        transit_days_max: int | None = None,
        freshness_status: str = "FRESH",
        effective_from: datetime | None = None,
        effective_to: datetime | None = None,
        source_system: str = "unknown",
        source_record_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        fetched_at: datetime | None = None,
        priority_tier: str | None = None,
        refresh_cadence: str | None = None,
    ) -> LaneRateBand:
        normalized_mode = lane_scope_service.normalize_mode(mode)
        normalized_service = lane_scope_service.normalize_service_level(service_level)
        normalized_origin = _normalize_country(origin_country)
        normalized_destination = _normalize_country(destination_country)
        if not normalized_origin or not normalized_destination:
            raise ValueError("origin_country and destination_country are required")
        effective_from = effective_from or _now()
        metadata = dict(source_metadata or {})
        payload = {
            "origin_country": normalized_origin,
            "origin_region": _normalize_text(origin_region),
            "destination_country": normalized_destination,
            "destination_region": _normalize_text(destination_region),
            "mode": normalized_mode,
            "service_level": normalized_service,
            "min_weight_kg": str(_as_decimal(min_weight_kg)) if min_weight_kg is not None else None,
            "max_weight_kg": str(_as_decimal(max_weight_kg)) if max_weight_kg is not None else None,
            "effective_from": effective_from.isoformat(),
            "source_system": source_system,
            "source_record_id": source_record_id,
        }
        record_hash = _hash_payload(payload)
        lane_context = LaneLookupContextDTO(
            origin_country=normalized_origin,
            origin_region=_normalize_text(origin_region),
            destination_country=normalized_destination,
            destination_region=_normalize_text(destination_region),
            mode=normalized_mode,
            service_level=normalized_service,
            weight_kg=_as_decimal(max_weight_kg) if max_weight_kg is not None else None,
            volume_cbm=_as_decimal(max_volume_cbm) if max_volume_cbm is not None else None,
        )
        lane_key = lane_scope_service.build_lane_key(context=lane_context) or f"{normalized_origin}->{normalized_destination}|{normalized_mode}"
        request_hash = _hash_payload({"lane_key": lane_key, "payload": payload})
        run_log = self._get_or_create_run_log(db, provider=source_system, request_hash=request_hash, scope_key=lane_key)

        row = (
            db.query(LaneRateBand)
            .filter(
                or_(
                    LaneRateBand.source_record_hash == record_hash,
                    and_(LaneRateBand.source_record_id == source_record_id, LaneRateBand.source_record_id.isnot(None)),
                )
            )
            .first()
        )
        if row is None:
            self._close_overlaps(
                db,
                origin_country=normalized_origin,
                destination_country=normalized_destination,
                mode=normalized_mode,
                service_level=normalized_service,
                origin_region=_normalize_text(origin_region),
                destination_region=_normalize_text(destination_region),
                min_weight_kg=_as_decimal(min_weight_kg) if min_weight_kg is not None else None,
                max_weight_kg=_as_decimal(max_weight_kg) if max_weight_kg is not None else None,
                effective_from=effective_from,
                preserve_record_id=source_record_id,
            )
            row = LaneRateBand(
                origin_country=normalized_origin,
                origin_region=_normalize_text(origin_region),
                destination_country=normalized_destination,
                destination_region=_normalize_text(destination_region),
                mode=normalized_mode,
                service_level=normalized_service,
                min_weight_kg=_as_decimal(min_weight_kg) if min_weight_kg is not None else None,
                max_weight_kg=_as_decimal(max_weight_kg) if max_weight_kg is not None else None,
                min_volume_cbm=_as_decimal(min_volume_cbm) if min_volume_cbm is not None else None,
                max_volume_cbm=_as_decimal(max_volume_cbm) if max_volume_cbm is not None else None,
                currency=currency,
                rate_type=rate_type,
                rate_value=_as_decimal(rate_value),
                min_charge=_as_decimal(min_charge) if min_charge is not None else None,
                transit_days_min=transit_days_min,
                transit_days_max=transit_days_max,
                freshness_status=freshness_status,
                effective_from=effective_from,
                effective_to=effective_to,
                source_system=source_system,
                source_record_id=source_record_id,
                source_record_hash=record_hash,
                source_metadata=metadata,
            )
            db.add(row)
        else:
            row.origin_region = _normalize_text(origin_region)
            row.destination_region = _normalize_text(destination_region)
            row.mode = normalized_mode
            row.service_level = normalized_service
            row.min_weight_kg = _as_decimal(min_weight_kg) if min_weight_kg is not None else None
            row.max_weight_kg = _as_decimal(max_weight_kg) if max_weight_kg is not None else None
            row.min_volume_cbm = _as_decimal(min_volume_cbm) if min_volume_cbm is not None else None
            row.max_volume_cbm = _as_decimal(max_volume_cbm) if max_volume_cbm is not None else None
            row.currency = currency
            row.rate_type = rate_type
            row.rate_value = _as_decimal(rate_value)
            row.min_charge = _as_decimal(min_charge) if min_charge is not None else None
            row.transit_days_min = transit_days_min
            row.transit_days_max = transit_days_max
            row.freshness_status = freshness_status
            row.effective_from = effective_from
            row.effective_to = effective_to
            row.source_system = source_system
            row.source_metadata = metadata
            row.updated_at = _now()
        db.flush()

        registry = lane_scope_service.register_lane_activity(
            db,
            context=lane_context,
            source=source_system,
            source_metadata={
                **metadata,
                "scope_status": "covered",
                "priority_tier": priority_tier,
                "refresh_cadence": refresh_cadence,
                "source_record_id": source_record_id,
            },
            touched_at=fetched_at or _now(),
        )
        if registry is not None:
            registry.last_refreshed_at = fetched_at or _now()
            registry.scope_status = "covered"
            db.flush()

        run_log.records_written = 1
        run_log.status = "success"
        run_log.completed_at = _now()
        run_log.source_metadata = {"lane_key": lane_key, "lane_rate_band_id": row.id}
        return row


lane_rate_ingestion_service = LaneRateIngestionService()