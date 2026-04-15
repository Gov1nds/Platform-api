"""
Phase 2A Batch 3 / Phase 2B Batch 3: lane rate band lookup service.

Behavior:
- looks up market.lane_rate_bands using normalized lane context
- supports broader lane, mode, and service-level coverage
- derives/registers lane scope from real system activity
- preserves effective-date correctness
- returns explicit in-scope / missing / out-of-scope freight coverage states
- does not assume freight = 0 when no band exists
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.bom import BOM, BOMPart
from app.models.enrichment import EnrichmentRunLog, LaneRateBand
from app.models.logistics import Shipment
from app.models.project import Project
from app.schemas.enrichment import LaneLookupContextDTO, LaneRateLookupDTO
from app.services.enrichment.lane_scope_service import lane_scope_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _to_utc_datetime(value: date | datetime | None) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().upper()
    return value[:3] if value else None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _ci_equal(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().lower() == right.strip().lower()


def _meta_get(data: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


@contextmanager
def _run_log(
    db: Session,
    *,
    bom_part: BOMPart | None,
    project: Project | None,
    idempotency_key: str,
    request_hash: str,
):
    started = _now()
    log = EnrichmentRunLog(
        bom_id=bom_part.bom_id if bom_part else (project.bom_id if project else None),
        bom_part_id=bom_part.id if bom_part else None,
        project_id=project.id if project else None,
        run_scope="bom_line" if bom_part else "project",
        stage="lane_rate_band_lookup",
        provider="lane_rate_bands",
        status="started",
        idempotency_key=idempotency_key,
        attempt_count=1,
        request_hash=request_hash,
        source_system="platform-api",
        source_metadata={},
        started_at=started,
    )
    db.add(log)
    db.flush()
    try:
        yield log
        log.status = "success"
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)[:500]
        raise
    finally:
        completed = _now()
        log.completed_at = completed
        log.duration_ms = int((completed - started).total_seconds() * 1000)
        log.updated_at = completed


class LaneRateBandLookupService:
    STAGE = "lane_rate_band_lookup"

    def normalize_context(
        self,
        *,
        context: LaneLookupContextDTO | None = None,
        project: Project | None = None,
        shipment: Shipment | None = None,
        bom: BOM | None = None,
    ) -> LaneLookupContextDTO:
        project_meta = project.project_metadata if project else {}
        shipment_meta = shipment.shipment_metadata if shipment else {}

        origin_country = _normalize_country(
            (context.origin_country if context else None)
            or _meta_get(shipment_meta, "origin_country", "ship_from_country", "vendor_ship_from_country")
            or _meta_get(project_meta, "origin_country", "ship_from_country", "vendor_ship_from_country")
        )
        destination_country = _normalize_country(
            (context.destination_country if context else None)
            or _meta_get(shipment_meta, "destination_country", "ship_to_country", "buyer_destination_country")
            or _meta_get(project_meta, "destination_country", "ship_to_country", "buyer_destination_country")
        )

        origin_region = _normalize_text(
            (context.origin_region if context else None)
            or _meta_get(shipment_meta, "origin_region", "ship_from_region", "vendor_ship_from_region")
            or _meta_get(project_meta, "origin_region", "ship_from_region", "vendor_ship_from_region")
            or (shipment.origin if shipment else None)
        )
        destination_region = _normalize_text(
            (context.destination_region if context else None)
            or _meta_get(shipment_meta, "destination_region", "ship_to_region", "buyer_destination_region")
            or _meta_get(project_meta, "destination_region", "ship_to_region", "buyer_destination_region")
            or (shipment.destination if shipment else None)
            or (bom.delivery_location if bom else None)
        )

        mode = lane_scope_service.normalize_mode(
            (context.mode if context else None)
            or _meta_get(shipment_meta, "mode", "shipping_mode")
            or _meta_get(project_meta, "mode", "shipping_mode")
            or "sea"
        )
        service_level = lane_scope_service.normalize_service_level(
            (context.service_level if context else None)
            or _meta_get(shipment_meta, "service_level", "shipping_service_level")
            or _meta_get(project_meta, "service_level", "shipping_service_level")
        )

        weight_kg = (
            context.weight_kg
            if context and context.weight_kg is not None
            else _as_decimal(_meta_get(shipment_meta, "weight_kg", "estimated_weight_kg"))
            if shipment
            else None
        )
        volume_cbm = (
            context.volume_cbm
            if context and context.volume_cbm is not None
            else _as_decimal(_meta_get(shipment_meta, "volume_cbm", "estimated_volume_cbm"))
            if shipment
            else None
        )

        return LaneLookupContextDTO(
            origin_country=origin_country,
            origin_region=origin_region,
            destination_country=destination_country,
            destination_region=destination_region,
            mode=mode,
            service_level=service_level,
            weight_kg=weight_kg,
            volume_cbm=volume_cbm,
        )

    def _weight_match(self, row: LaneRateBand, weight_kg: Decimal | None) -> tuple[int, bool]:
        if weight_kg is None:
            return 0, False
        min_w = row.min_weight_kg
        max_w = row.max_weight_kg
        min_ok = min_w is None or weight_kg >= _as_decimal(min_w)
        max_ok = max_w is None or weight_kg <= _as_decimal(max_w)
        if min_ok and max_ok:
            return 3, True
        return -10, False

    def _volume_match(self, row: LaneRateBand, volume_cbm: Decimal | None) -> tuple[int, bool]:
        if volume_cbm is None:
            return 0, False
        min_v = row.min_volume_cbm
        max_v = row.max_volume_cbm
        min_ok = min_v is None or volume_cbm >= _as_decimal(min_v)
        max_ok = max_v is None or volume_cbm <= _as_decimal(max_v)
        if min_ok and max_ok:
            return 2, True
        return -10, False

    def _row_specificity_score(self, row: LaneRateBand, ctx: LaneLookupContextDTO) -> int:
        score = 0

        row_service = lane_scope_service.normalize_service_level(row.service_level)
        ctx_service = lane_scope_service.normalize_service_level(ctx.service_level)
        if ctx_service:
            if row_service and row_service == ctx_service:
                score += 5
            elif row_service:
                score -= 3
        elif row_service is None:
            score += 1

        if ctx.origin_region:
            if row.origin_region and _ci_equal(row.origin_region, ctx.origin_region):
                score += 4
            elif row.origin_region:
                score -= 2

        if ctx.destination_region:
            if row.destination_region and _ci_equal(row.destination_region, ctx.destination_region):
                score += 4
            elif row.destination_region:
                score -= 2

        weight_score, weight_used = self._weight_match(row, ctx.weight_kg)
        volume_score, volume_used = self._volume_match(row, ctx.volume_cbm)

        if weight_used:
            score += weight_score
        if volume_used:
            score += volume_score

        return score

    def _candidate_rows(
        self,
        db: Session,
        *,
        ctx: LaneLookupContextDTO,
        as_of: datetime,
    ) -> list[LaneRateBand]:
        filters = [
            LaneRateBand.origin_country == ctx.origin_country,
            LaneRateBand.destination_country == ctx.destination_country,
            LaneRateBand.mode == lane_scope_service.normalize_mode(ctx.mode),
            LaneRateBand.effective_from <= as_of,
            or_(LaneRateBand.effective_to.is_(None), LaneRateBand.effective_to > as_of),
        ]
        if ctx.service_level:
            filters.append(
                or_(
                    LaneRateBand.service_level == lane_scope_service.normalize_service_level(ctx.service_level),
                    LaneRateBand.service_level.is_(None),
                )
            )
        return (
            db.query(LaneRateBand)
            .filter(and_(*filters))
            .order_by(
                LaneRateBand.effective_from.desc(),
                LaneRateBand.updated_at.desc(),
            )
            .all()
        )

    def _compute_estimate(
        self,
        *,
        row: LaneRateBand,
        weight_kg: Decimal | None,
        volume_cbm: Decimal | None,
    ) -> Decimal | None:
        rate_value = _as_decimal(row.rate_value)
        min_charge = _as_decimal(row.min_charge) if row.min_charge is not None else None
        rate_type = (row.rate_type or "per_kg").lower()

        estimate = None
        if rate_type == "per_kg" and weight_kg is not None:
            estimate = rate_value * weight_kg
        elif rate_type == "per_cbm" and volume_cbm is not None:
            estimate = rate_value * volume_cbm
        elif rate_type in {"flat", "fixed"}:
            estimate = rate_value
        elif rate_type == "per_shipment":
            estimate = rate_value

        if estimate is not None and min_charge is not None and estimate < min_charge:
            estimate = min_charge
        return estimate

    def _missing_result(
        self,
        *,
        bom_part_id: str | None,
        project_id: str | None,
        ctx: LaneLookupContextDTO,
        lookup_date: datetime,
        reason: str,
        coverage_status: str,
        scope_row,
    ) -> LaneRateLookupDTO:
        lane_key = lane_scope_service.build_lane_key(context=ctx)
        return LaneRateLookupDTO(
            bom_part_id=bom_part_id,
            project_id=project_id,
            resolved=False,
            lookup_status="uncertain",
            origin_country=ctx.origin_country,
            origin_region=ctx.origin_region,
            destination_country=ctx.destination_country,
            destination_region=ctx.destination_region,
            mode=ctx.mode,
            service_level=ctx.service_level,
            lookup_date=lookup_date,
            lane_key=lane_key,
            coverage_status=coverage_status,
            priority_tier=scope_row.priority_tier if scope_row else None,
            refresh_cadence=scope_row.refresh_cadence if scope_row else None,
            last_refreshed_at=scope_row.last_refreshed_at if scope_row else None,
            confidence=Decimal("0"),
            uncertainty_reason=reason,
            source_metadata={
                "scope_status": scope_row.scope_status if scope_row else None,
                "lane_key": lane_key,
            },
        )

    def lookup_lane_rate(
        self,
        db: Session,
        *,
        context: LaneLookupContextDTO | None = None,
        project: Project | None = None,
        shipment: Shipment | None = None,
        bom: BOM | None = None,
        bom_part: BOMPart | None = None,
        lookup_date: date | datetime | None = None,
        trace_id: str | None = None,
    ) -> LaneRateLookupDTO:
        ctx = self.normalize_context(context=context, project=project, shipment=shipment, bom=bom)
        as_of = _to_utc_datetime(lookup_date)
        lane_key = lane_scope_service.build_lane_key(context=ctx)

        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part.id if bom_part else None,
                "project_id": project.id if project else None,
                "origin_country": ctx.origin_country,
                "origin_region": ctx.origin_region,
                "destination_country": ctx.destination_country,
                "destination_region": ctx.destination_region,
                "mode": ctx.mode,
                "service_level": ctx.service_level,
                "weight_kg": str(ctx.weight_kg) if ctx.weight_kg is not None else None,
                "volume_cbm": str(ctx.volume_cbm) if ctx.volume_cbm is not None else None,
                "lookup_date": as_of.isoformat(),
                "trace_id": trace_id,
            }
        )
        idempotency_key = f"{self.STAGE}:{bom_part.id if bom_part else project.id if project else 'none'}:{request_hash}"

        with _run_log(
            db,
            bom_part=bom_part,
            project=project,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ) as run_log:
            scope_row = None
            if lane_key:
                scope_row = lane_scope_service.register_lane_activity(
                    db,
                    context=ctx,
                    source="platform-api",
                    source_metadata={
                        "trace_id": trace_id,
                        "project_id": project.id if project else None,
                        "bom_part_id": bom_part.id if bom_part else None,
                    },
                    touched_at=as_of,
                )

            if not ctx.origin_country or not ctx.destination_country:
                result = self._missing_result(
                    bom_part_id=bom_part.id if bom_part else None,
                    project_id=project.id if project else None,
                    ctx=ctx,
                    lookup_date=as_of,
                    reason="lane_context_incomplete",
                    coverage_status="unknown",
                    scope_row=scope_row,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            rows = self._candidate_rows(db, ctx=ctx, as_of=as_of)
            if not rows:
                scope_status = str(scope_row.scope_status or "").lower() if scope_row is not None else ""
                coverage_status = "missing" if scope_status in {"covered", "active"} else "out_of_scope"
                reason = "missing_lane_coverage" if coverage_status == "missing" else "lane_out_of_scope"
                result = self._missing_result(
                    bom_part_id=bom_part.id if bom_part else None,
                    project_id=project.id if project else None,
                    ctx=ctx,
                    lookup_date=as_of,
                    reason=reason,
                    coverage_status=coverage_status,
                    scope_row=scope_row,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                    "coverage_status": result.coverage_status,
                }
                return result

            ranked = sorted(
                rows,
                key=lambda row: (
                    self._row_specificity_score(row, ctx),
                    row.effective_from.timestamp() if row.effective_from else 0,
                    row.updated_at.timestamp() if row.updated_at else 0,
                ),
                reverse=True,
            )
            selected = ranked[0]

            p50_estimate = self._compute_estimate(
                row=selected,
                weight_kg=ctx.weight_kg,
                volume_cbm=ctx.volume_cbm,
            )

            p90_estimate = p50_estimate
            if scope_row is not None:
                lane_scope_service.mark_lane_refreshed(
                    db,
                    lane_key=scope_row.lane_key,
                    source_metadata={"last_resolved_lane_rate_band_id": selected.id},
                )
                scope_row = lane_scope_service.get_scope_row(db, lane_key=scope_row.lane_key)

            result = LaneRateLookupDTO(
                bom_part_id=bom_part.id if bom_part else None,
                project_id=project.id if project else None,
                resolved=True,
                lookup_status="resolved",
                origin_country=selected.origin_country,
                origin_region=selected.origin_region,
                destination_country=selected.destination_country,
                destination_region=selected.destination_region,
                mode=selected.mode,
                service_level=selected.service_level or ctx.service_level,
                lookup_date=as_of,
                lane_key=lane_key,
                lane_rate_band_id=selected.id,
                currency=selected.currency,
                rate_type=selected.rate_type,
                rate_value=_as_decimal(selected.rate_value),
                min_charge=_as_decimal(selected.min_charge) if selected.min_charge is not None else None,
                p50_freight_estimate=p50_estimate,
                p90_freight_estimate=p90_estimate,
                transit_days_min=selected.transit_days_min,
                transit_days_max=selected.transit_days_max,
                confidence=Decimal("1") if str(selected.freshness_status or "").upper() == "FRESH" else Decimal("0.8"),
                freshness_status=selected.freshness_status,
                effective_from=selected.effective_from,
                effective_to=selected.effective_to,
                coverage_status="in_scope",
                priority_tier=scope_row.priority_tier if scope_row else None,
                refresh_cadence=scope_row.refresh_cadence if scope_row else None,
                last_refreshed_at=scope_row.last_refreshed_at if scope_row else None,
                uncertainty_reason=None,
                source_metadata={
                    "trace_id": trace_id,
                    "selection_mode": "best_valid_lane_band",
                    "p90_estimate_method": "single_band_proxy",
                    "weight_kg": str(ctx.weight_kg) if ctx.weight_kg is not None else None,
                    "volume_cbm": str(ctx.volume_cbm) if ctx.volume_cbm is not None else None,
                    "scope_status": scope_row.scope_status if scope_row else None,
                },
            )
            run_log.records_written = 1
            run_log.freshness_status = selected.freshness_status
            run_log.source_metadata = {
                "trace_id": trace_id,
                "lookup_status": result.lookup_status,
                "lane_rate_band_id": selected.id,
                "lane_key": lane_key,
                "coverage_status": result.coverage_status,
            }
            return result


lane_rate_band_lookup_service = LaneRateBandLookupService()