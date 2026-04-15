"""
Phase 2A / Phase 2B tariff schedule lookup service.

Behavior:
- consumes HS mapping resolution from Phase 2A
- preserves HS6 as the baseline tariff anchor
- supports broader jurisdiction coverage through market.tariff_scope_registry
- supports national extension context when the resolved HS code carries >6 digits
- preserves effective-date correctness without inventing zero tariffs
- surfaces explicit missing / out-of-scope / stale coverage states
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, asc, case, desc, or_
from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.enrichment import EnrichmentRunLog
from app.models.market import TariffSchedule, TariffScopeRegistry
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


def _normalize_hs_digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


def _normalize_hs6(value: str | None) -> str | None:
    digits = _normalize_hs_digits(value)
    if not digits or len(digits) < 6:
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
    FRESHNESS_STALE_STATES = {"STALE", "EXPIRED"}
    IN_SCOPE_LEVELS = {"full", "partial", "pilot", "limited"}

    def _scope_row(self, db: Session, *, destination_country: str | None) -> TariffScopeRegistry | None:
        if not destination_country:
            return None
        return (
            db.query(TariffScopeRegistry)
            .filter(TariffScopeRegistry.import_country == destination_country)
            .first()
        )

    def _scope_status(
        self,
        *,
        destination_country: str | None,
        scope_row: TariffScopeRegistry | None,
    ) -> tuple[str, str | None]:
        if not destination_country:
            return "unknown", "destination_country_missing"
        if scope_row is None:
            return "out_of_scope", "tariff_jurisdiction_out_of_scope"

        coverage_level = str(scope_row.coverage_level or "unknown").strip().lower()
        if coverage_level in self.IN_SCOPE_LEVELS:
            return "in_scope", None
        return "out_of_scope", "tariff_jurisdiction_out_of_scope"

    def _hs_context(self, hs_resolution: HSResolutionDTO | None) -> tuple[str | None, str | None]:
        hs_code = hs_resolution.hs_code if hs_resolution else None
        digits = _normalize_hs_digits(hs_code)
        if not digits or len(digits) <= 6:
            return _normalize_hs6(hs_code), None
        return digits[:6], digits

    def _base_result(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO | None,
        destination_country: str | None,
        origin_country: str | None,
        as_of: datetime,
        scope_row: TariffScopeRegistry | None,
        coverage_status: str,
    ) -> TariffLookupDTO:
        hs6, extension_code = self._hs_context(hs_resolution)
        return TariffLookupDTO(
            bom_part_id=bom_part_id,
            resolved=False,
            lookup_status="uncertain",
            hs_code=hs_resolution.hs_code if hs_resolution else None,
            hs6=hs6,
            hs_version=hs_resolution.hs_version if hs_resolution else None,
            national_extension_code=extension_code,
            tariff_code_type="national_extension" if extension_code else "HS6",
            destination_country=destination_country,
            import_country=destination_country,
            origin_country=origin_country,
            lookup_date=as_of,
            confidence=Decimal("0"),
            coverage_level=scope_row.coverage_level if scope_row else None,
            coverage_status=coverage_status,
            last_ingested_at=scope_row.last_ingested_at if scope_row else None,
            source_metadata={
                "hs_resolution_status": hs_resolution.resolution_status if hs_resolution else None,
                "scope_source": scope_row.source if scope_row else None,
                "scope_update_cadence": scope_row.update_cadence if scope_row else None,
            },
        )

    def _build_hs_uncertainty(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO | None,
        destination_country: str | None,
        origin_country: str | None,
        as_of: datetime,
        scope_row: TariffScopeRegistry | None,
        coverage_status: str,
    ) -> TariffLookupDTO:
        result = self._base_result(
            bom_part_id=bom_part_id,
            hs_resolution=hs_resolution,
            destination_country=destination_country,
            origin_country=origin_country,
            as_of=as_of,
            scope_row=scope_row,
            coverage_status=coverage_status,
        )
        reason = "hs_mapping_missing"
        if hs_resolution is not None:
            if hs_resolution.resolution_status == "needs_review":
                reason = hs_resolution.uncertainty_reason or "hs_mapping_low_confidence"
            elif hs_resolution.resolution_status == "unresolved":
                reason = hs_resolution.uncertainty_reason or "hs_mapping_missing"
        result.uncertainty_reason = reason
        return result

    def _build_scope_uncertainty(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO | None,
        destination_country: str | None,
        origin_country: str | None,
        as_of: datetime,
        scope_row: TariffScopeRegistry | None,
        coverage_status: str,
        reason: str,
    ) -> TariffLookupDTO:
        result = self._base_result(
            bom_part_id=bom_part_id,
            hs_resolution=hs_resolution,
            destination_country=destination_country,
            origin_country=origin_country,
            as_of=as_of,
            scope_row=scope_row,
            coverage_status=coverage_status,
        )
        result.uncertainty_reason = reason
        return result

    def _build_missing_tariff(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO,
        destination_country: str,
        origin_country: str | None,
        as_of: datetime,
        scope_row: TariffScopeRegistry | None,
        coverage_status: str,
    ) -> TariffLookupDTO:
        result = self._base_result(
            bom_part_id=bom_part_id,
            hs_resolution=hs_resolution,
            destination_country=destination_country,
            origin_country=origin_country,
            as_of=as_of,
            scope_row=scope_row,
            coverage_status=coverage_status,
        )
        result.uncertainty_reason = "no_tariff_schedule_found"
        result.source_metadata.update(
            {
                "matched_hs_code": _normalize_hs6(hs_resolution.hs_code),
            }
        )
        return result

    def _build_result(
        self,
        *,
        bom_part_id: str | None,
        hs_resolution: HSResolutionDTO,
        row: TariffSchedule,
        as_of: datetime,
        customs_value: Decimal | None,
        matched_on_origin: str,
        matched_on_tariff_code: str,
        scope_row: TariffScopeRegistry | None,
        coverage_status: str,
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

        freshness_status = row.freshness_status
        if (
            scope_row is not None
            and scope_row.last_ingested_at is not None
            and row.fetched_at is not None
            and scope_row.last_ingested_at > row.fetched_at
            and str(freshness_status or "").upper() == "FRESH"
        ):
            freshness_status = "STALE"

        hs_digits = _normalize_hs_digits(hs_resolution.hs_code)
        national_extension_code = row.national_extension_code or (hs_digits if hs_digits and len(hs_digits) > 6 else None)
        tariff_code_type = row.tariff_code_type or ("national_extension" if national_extension_code else "HS6")

        return TariffLookupDTO(
            bom_part_id=bom_part_id,
            resolved=True,
            lookup_status="resolved",
            hs_code=hs_resolution.hs_code,
            hs6=row.hs6 or _normalize_hs6(hs_resolution.hs_code),
            hs_version=row.hs_version or hs_resolution.hs_version,
            national_extension_code=national_extension_code,
            tariff_code_type=tariff_code_type,
            destination_country=_normalize_country(row.destination_country),
            import_country=_normalize_country(row.import_country) or _normalize_country(row.destination_country),
            origin_country=_normalize_country(row.origin_country),
            lookup_date=as_of,
            tariff_schedule_id=row.id,
            duty_rate_pct=duty_rate_pct,
            additional_taxes_pct=additional_taxes_pct,
            total_tariff_rate_pct=total_rate_pct,
            confidence=_as_decimal(row.confidence),
            source=row.source,
            freshness_status=freshness_status,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            coverage_level=row.coverage_level or (scope_row.coverage_level if scope_row else None),
            coverage_status=coverage_status,
            last_ingested_at=scope_row.last_ingested_at if scope_row else None,
            estimated_customs_value=customs_value,
            estimated_duty=estimated_duty,
            estimated_additional_taxes=estimated_additional_taxes,
            estimated_total_tariff=estimated_total_tariff,
            uncertainty_reason=None,
            source_metadata={
                "matched_on_origin": matched_on_origin,
                "matched_on_tariff_code": matched_on_tariff_code,
                "hs_resolution_status": hs_resolution.resolution_status,
                "tariff_hs_code": row.hs_code,
                "scope_update_cadence": scope_row.update_cadence if scope_row else None,
                "schedule_source_record_id": row.source_record_id,
            },
        )

    def _candidate_query(
        self,
        db: Session,
        *,
        hs6: str,
        national_extension_code: str | None,
        destination_country: str,
        origin_country: str | None,
        as_of: datetime,
        hs_version: str | None,
    ):
        filters = [
            TariffSchedule.destination_country == destination_country,
            TariffSchedule.effective_from <= as_of,
            or_(TariffSchedule.effective_to.is_(None), TariffSchedule.effective_to > as_of),
            or_(
                TariffSchedule.hs6 == hs6,
                and_(TariffSchedule.hs6.is_(None), TariffSchedule.hs_code == hs6),
                TariffSchedule.hs_code == hs6,
                TariffSchedule.hs_code == (national_extension_code or "__noext__"),
                TariffSchedule.national_extension_code == national_extension_code,
            ),
        ]

        if hs_version:
            filters.append(or_(TariffSchedule.hs_version.is_(None), TariffSchedule.hs_version == hs_version))

        if origin_country:
            filters.append(
                or_(
                    TariffSchedule.origin_country == origin_country,
                    TariffSchedule.origin_country.in_(self.GENERAL_ORIGIN_CODES),
                )
            )
        else:
            filters.append(TariffSchedule.origin_country.in_(self.GENERAL_ORIGIN_CODES))

        tariff_code_rank = case(
            (TariffSchedule.national_extension_code == national_extension_code, 0),
            (TariffSchedule.hs_code == (national_extension_code or "__noext__"), 1),
            (TariffSchedule.hs6 == hs6, 2),
            (TariffSchedule.hs_code == hs6, 3),
            else_=4,
        )
        origin_rank = case(
            (TariffSchedule.origin_country == origin_country, 0),
            else_=1,
        )

        return (
            db.query(TariffSchedule)
            .filter(and_(*filters))
            .order_by(
                asc(tariff_code_rank),
                asc(origin_rank),
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
        national_extension_code: str | None,
        hs6: str,
    ) -> tuple[TariffSchedule, str, str] | None:
        if not rows:
            return None

        normalized_origin = _normalize_country(origin_country)

        def _origin_match(row: TariffSchedule) -> str | None:
            row_origin = _normalize_country(row.origin_country)
            if normalized_origin and row_origin == normalized_origin:
                return "origin_country_exact"
            if row_origin in self.GENERAL_ORIGIN_CODES:
                return "origin_country_general"
            return None

        def _code_match(row: TariffSchedule) -> str | None:
            row_ext = _normalize_hs_digits(row.national_extension_code)
            row_hs = _normalize_hs_digits(row.hs_code)
            row_hs6 = _normalize_hs_digits(row.hs6) or (_normalize_hs6(row_hs) if row_hs else None)
            if national_extension_code and row_ext == national_extension_code:
                return "national_extension_exact"
            if national_extension_code and row_hs == national_extension_code:
                return "national_extension_from_hs_code"
            if row_hs6 == hs6 or row_hs == hs6:
                return "hs6_baseline"
            return None

        for row in rows:
            origin_match = _origin_match(row)
            code_match = _code_match(row)
            if origin_match and code_match:
                return row, origin_match, code_match
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

        scope_row = self._scope_row(db, destination_country=normalized_destination)
        coverage_status, coverage_reason = self._scope_status(
            destination_country=normalized_destination,
            scope_row=scope_row,
        )

        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part_id,
                "destination_country": normalized_destination,
                "origin_country": normalized_origin,
                "lookup_date": as_of.isoformat(),
                "hs_code": hs_resolution.hs_code if hs_resolution else None,
                "hs_resolution_status": hs_resolution.resolution_status if hs_resolution else None,
                "customs_value": str(normalized_customs_value) if normalized_customs_value is not None else None,
                "coverage_status": coverage_status,
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
                result = self._build_scope_uncertainty(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=None,
                    origin_country=normalized_origin,
                    as_of=as_of,
                    scope_row=scope_row,
                    coverage_status=coverage_status,
                    reason="destination_country_missing",
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            if coverage_status != "in_scope":
                result = self._build_scope_uncertainty(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                    scope_row=scope_row,
                    coverage_status=coverage_status,
                    reason=coverage_reason or "tariff_jurisdiction_out_of_scope",
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                    "coverage_status": coverage_status,
                }
                return result

            if hs_resolution is None or not hs_resolution.resolved:
                result = self._build_hs_uncertainty(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                    scope_row=scope_row,
                    coverage_status=coverage_status,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            hs6, national_extension_code = self._hs_context(hs_resolution)
            if not hs6:
                result = self._build_scope_uncertainty(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                    scope_row=scope_row,
                    coverage_status=coverage_status,
                    reason="invalid_hs_code_for_tariff_lookup",
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
                national_extension_code=national_extension_code,
                destination_country=normalized_destination,
                origin_country=normalized_origin,
                as_of=as_of,
                hs_version=hs_resolution.hs_version,
            ).all()

            selected = self._select_best_row(
                rows,
                origin_country=normalized_origin,
                national_extension_code=national_extension_code,
                hs6=hs6,
            )
            if selected is None:
                result = self._build_missing_tariff(
                    bom_part_id=bom_part_id,
                    hs_resolution=hs_resolution,
                    destination_country=normalized_destination,
                    origin_country=normalized_origin,
                    as_of=as_of,
                    scope_row=scope_row,
                    coverage_status=coverage_status,
                )
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "lookup_status": result.lookup_status,
                    "uncertainty_reason": result.uncertainty_reason,
                    "hs6": hs6,
                    "national_extension_code": national_extension_code,
                }
                return result

            row, matched_on_origin, matched_on_tariff_code = selected
            result = self._build_result(
                bom_part_id=bom_part_id,
                hs_resolution=hs_resolution,
                row=row,
                as_of=as_of,
                customs_value=normalized_customs_value,
                matched_on_origin=matched_on_origin,
                matched_on_tariff_code=matched_on_tariff_code,
                scope_row=scope_row,
                coverage_status=coverage_status,
            )
            run_log.records_written = 1
            run_log.freshness_status = result.freshness_status
            run_log.source_metadata = {
                "trace_id": trace_id,
                "lookup_status": result.lookup_status,
                "tariff_schedule_id": row.id,
                "matched_on_origin": matched_on_origin,
                "matched_on_tariff_code": matched_on_tariff_code,
                "hs6": hs6,
                "national_extension_code": national_extension_code,
                "coverage_status": coverage_status,
            }
            return result

    def lookup_tariff(self, db: Session, **kwargs: Any) -> TariffLookupDTO:
        return self.lookup_for_hs_resolution(db, **kwargs)


tariff_lookup_service = TariffLookupService()
