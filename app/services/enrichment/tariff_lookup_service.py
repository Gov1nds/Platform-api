"""
Phase 2A Batch 3 - Part 2: tariff schedule lookup service.

Behavior:
- consumes HS mapping resolution from Part 1
- looks up tariff_schedules using destination/import country, origin country, hs6, and effective date
- preserves effective-date correctness
- supports origin-specific rows first, then general/MFN-style fallback if present in existing data
- returns explicit uncertainty when HS is unresolved/low-confidence or when no tariff row is found
- does not assume zero tariff when no row exists
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.enrichment import EnrichmentRunLog
from app.models.market import TariffSchedule
from app.schemas.enrichment import HSResolutionDTO, TariffLookupDTO


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
    return value.strip().upper()


def _normalize_hs6(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 6:
        return None
    return digits[:6]


@contextmanager
def _run_log(
    db: Session,
    *,
    bom_part: BOMPart | None,
    idempotency_key: str,
    request_hash: str,
):
    started = _now()
    log = EnrichmentRunLog(
        bom_id=bom_part.bom_id if bom_part else None,
        bom_part_id=bom_part.id if bom_part else None,
        run_scope="bom_line" if bom_part else "batch",
        stage="tariff_lookup",
        provider="tariff_schedules",
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


class TariffLookupService:
    STAGE = "tariff_lookup"
    GENERAL_ORIGIN_CODES = ("ALL", "MFN", "GEN")

    def _build_hs_uncertainty(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO | None,
        destination_country: str | None,
        origin_country: str | None,
        as_of: datetime,
    ) -> TariffLookupDTO:
        reason = "hs_mapping_missing"
        if hs_resolution is not None:
            if hs_resolution.resolution_status == "needs_review":
                reason = hs_resolution.uncertainty_reason or "hs_mapping_low_confidence"
            elif hs_resolution.resolution_status == "unresolved":
                reason = hs_resolution.uncertainty_reason or "hs_mapping_missing"

        return TariffLookupDTO(
            bom_part_id=bom_part_id,
            resolved=False,
            lookup_status="uncertain",
            hs_code=hs_resolution.hs_code if hs_resolution else None,
            hs6=_normalize_hs6(hs_resolution.hs_code if hs_resolution else None),
            destination_country=destination_country,
            origin_country=origin_country,
            lookup_date=as_of,
            confidence=Decimal("0"),
            uncertainty_reason=reason,
            source_metadata={
                "hs_resolution_status": hs_resolution.resolution_status if hs_resolution else None,
                "hs_uncertainty_reason": hs_resolution.uncertainty_reason if hs_resolution else None,
            },
        )

    def _build_missing_tariff(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO,
        destination_country: str,
        origin_country: str | None,
        as_of: datetime,
    ) -> TariffLookupDTO:
        return TariffLookupDTO(
            bom_part_id=bom_part_id,
            resolved=False,
            lookup_status="uncertain",
            hs_code=hs_resolution.hs_code,
            hs6=_normalize_hs6(hs_resolution.hs_code),
            destination_country=destination_country,
            origin_country=origin_country,
            lookup_date=as_of,
            confidence=Decimal("0"),
            uncertainty_reason="no_tariff_schedule_found",
            source_metadata={
                "hs_resolution_status": hs_resolution.resolution_status,
                "matched_hs_code": _normalize_hs6(hs_resolution.hs_code),
            },
        )

    def _build_result(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO,
        row: TariffSchedule,
        as_of: datetime,
        customs_value: Decimal | None,
        matched_on_origin: str,
    ) -> TariffLookupDTO:
        duty_rate_pct = _as_decimal(row.duty_rate_pct)
        additional_taxes_pct = _as_decimal(row.additional_taxes_pct)
        total_rate_pct = duty_rate_pct + additional_taxes_pct

        estimated_duty = None
        estimated_additional_taxes = None
        estimated_total_tariff = None
        if customs_value is not None:
            estimated_duty = (customs_value * duty_rate_pct) / Decimal("100")
            estimated_additional_taxes = (customs_value * additional_taxes_pct) / Decimal("100")
            estimated_total_tariff = estimated_duty + estimated_additional_taxes

        return TariffLookupDTO(
            bom_part_id=bom_part_id,
            resolved=True,
            lookup_status="resolved",
            hs_code=hs_resolution.hs_code,
            hs6=_normalize_hs6(hs_resolution.hs_code),
            destination_country=_normalize_country(row.destination_country),
            origin_country=_normalize_country(row.origin_country),
            lookup_date=as_of,
            tariff_schedule_id=row.id,
            duty_rate_pct=duty_rate_pct,
            additional_taxes_pct=additional_taxes_pct,
            total_tariff_rate_pct=total_rate_pct,
            confidence=_as_decimal(row.confidence),
            source=row.source,
            freshness_status=row.freshness_status,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            estimated_customs_value=customs_value,
            estimated_duty=estimated_duty,
            estimated_additional_taxes=estimated_additional_taxes,
            estimated_total_tariff=estimated_total_tariff,
            uncertainty_reason=None,
            source_metadata={
                "matched_on_origin": matched_on_origin,
                "hs_resolution_status": hs_resolution.resolution_status,
                "tariff_hs_code": row.hs_code,
            },
        )

    def _candidate_query(
        self,
        db: Session,
        *,
        hs6: str,
        destination_country: str,
        origin_country: str | None,
        as_of: datetime,
    ):
        filters = [
            TariffSchedule.hs_code == hs6,
            TariffSchedule.destination_country == destination_country,
            TariffSchedule.effective_from <= as_of,
            or_(TariffSchedule.effective_to.is_(None), TariffSchedule.effective_to > as_of),
        ]

        if origin_country:
            filters.append(
                or_(
                    TariffSchedule.origin_country == origin_country,
                    TariffSchedule.origin_country.in_(self.GENERAL_ORIGIN_CODES),
                )
            )
        else:
            filters.append(TariffSchedule.origin_country.in_(self.GENERAL_ORIGIN_CODES))

        return (
            db.query(TariffSchedule)
            .filter(and_(*filters))
            .order_by(
                desc(TariffSchedule.effective_from),
                desc(TariffSchedule.confidence),
                desc(TariffSchedule.created_at),
            )
        )

    def _select_best_row(
        self,
        rows: list[TariffSchedule],
        *,
        origin_country: str | None,
    ) -> tuple[TariffSchedule, str] | None:
        if not rows:
            return None

        normalized_origin = _normalize_country(origin_country)
        exact_rows: list[TariffSchedule] = []
        fallback_rows: list[TariffSchedule] = []

        for row in rows:
            row_origin = _normalize_country(row.origin_country)
            if normalized_origin and row_origin == normalized_origin:
                exact_rows.append(row)
            elif row_origin in self.GENERAL_ORIGIN_CODES:
                fallback_rows.append(row)

        if exact_rows:
            return exact_rows[0], "origin_country_exact"
        if fallback_rows:
            return fallback_rows[0], "origin_country_general"
        return None

    def lookup_for_hs_resolution(
        self,
        db: Session,
        *,
        hs_resolution: HSResolutionDTO | None,
        destination_country: str,
        origin_country: str | None = None,
        lookup_date: date | datetime | None = None,
        customs_value: Decimal | None = None,
        bom_part: BOMPart | None = None,
        trace_id: str | None = None,
    ) -> TariffLookupDTO:
        as_of = _to_utc_datetime(lookup_date)
        normalized_destination = _normalize_country(destination_country)
        normalized_origin = _normalize_country(origin_country)
        normalized_customs_value = _as_decimal(customs_value) if customs_value is not None else None
        bom_part_id = bom_part.id if bom_part else (hs_resolution.bom_part_id if hs_resolution else None)

        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part_id,
                "destination_country": normalized_destination,
                "origin_country": normalized_origin,
                "lookup_date": as_of.isoformat(),
                "hs_code": hs_resolution.hs_code if hs_resolution else None,
                "hs_resolution_status": hs_resolution.resolution_status if hs_resolution else None,
                "customs_value": str(normalized_customs_value) if normalized_customs_value is not None else None,
                "trace_id": trace_id,
            }
        )
        idempotency_key = f"{self.STAGE}:{bom_part_id or 'none'}:{request_hash}"

        with _run_log(
            db,
            bom_part=bom_part,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ) as run_log:
            if not normalized_destination:
                result = TariffLookupDTO(
                    bom_part_id=bom_part_id,
                    resolved=False,
                    lookup_status="uncertain",
                    hs_code=hs_resolution.hs_code if hs_resolution else None,
                    hs6=_normalize_hs6(hs_resolution.hs_code if hs_resolution else None),
                    destination_country=None,
                    origin_country=normalized_origin,
                    lookup_date=as_of,
                    confidence=Decimal("0"),
                    uncertainty_reason="destination_country_missing",
                    source_metadata={},
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            if hs_resolution is None or not hs_resolution.resolved:
                result = self._build_hs_uncertainty(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            hs6 = _normalize_hs6(hs_resolution.hs_code)
            if not hs6:
                result = TariffLookupDTO(
                    bom_part_id=bom_part_id,
                    resolved=False,
                    lookup_status="uncertain",
                    hs_code=hs_resolution.hs_code,
                    hs6=None,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    lookup_date=as_of,
                    confidence=Decimal("0"),
                    uncertainty_reason="invalid_hs_code_for_tariff_lookup",
                    source_metadata={
                        "hs_resolution_status": hs_resolution.resolution_status,
                    },
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            rows = self._candidate_query(
                db,
                hs6=hs6,
                destination_country=normalized_destination,
                origin_country=normalized_origin,
                as_of=as_of,
            ).all()

            selected = self._select_best_row(rows, origin_country=normalized_origin)
            if selected is None:
                result = self._build_missing_tariff(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                    "hs6": hs6,
                }
                return result

            row, matched_on_origin = selected
            result = self._build_result(
                bom_part_id=bom_part_id,
                hs_resolution=hs_resolution,
                row=row,
                as_of=as_of,
                customs_value=normalized_customs_value,
                matched_on_origin=matched_on_origin,
            )
            run_log.records_written = 1
            run_log.freshness_status = row.freshness_status
            run_log.source_metadata = {
                "trace_id": trace_id,
                "lookup_status": result.lookup_status,
                "tariff_schedule_id": row.id,
                "matched_on_origin": matched_on_origin,
                "hs6": hs6,
            }
            return result


tariff_lookup_service = TariffLookupService()