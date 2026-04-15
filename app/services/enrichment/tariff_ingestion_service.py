"""
Phase 2B Batch 2: tariff scope expansion and broader trade coverage ingestion.

Behavior:
- maintains market.tariff_scope_registry for jurisdiction coverage tracking
- ingests tariff schedules append-only with effective-date awareness
- preserves HS6 as the baseline anchor while allowing national extensions
- avoids destructive overwrites of historical effective windows
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.enrichment import EnrichmentRunLog
from app.models.market import TariffSchedule, TariffScopeRegistry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().upper()


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


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class TariffIngestionService:
    STAGE = "tariff_ingestion"
    DEFAULT_TTL_SECONDS = 604800

    def _get_or_create_run_log(
        self,
        db: Session,
        *,
        provider: str,
        request_hash: str,
        scope_key: str,
    ) -> EnrichmentRunLog:
        idempotency_key = f"{self.STAGE}:{scope_key}:{request_hash}"
        existing = (
            db.query(EnrichmentRunLog)
            .filter(EnrichmentRunLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing

        log = EnrichmentRunLog(
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
        db.add(log)
        db.flush()
        return log

    def _upsert_scope_registry(
        self,
        db: Session,
        *,
        import_country: str,
        coverage_level: str,
        update_cadence: str | None,
        last_ingested_at: datetime,
        source: str | None,
        source_metadata: dict[str, Any] | None,
    ) -> TariffScopeRegistry:
        row = (
            db.query(TariffScopeRegistry)
            .filter(TariffScopeRegistry.import_country == import_country)
            .first()
        )
        metadata = dict(source_metadata or {})
        if row is None:
            row = TariffScopeRegistry(
                import_country=import_country,
                coverage_level=coverage_level,
                update_cadence=update_cadence,
                last_ingested_at=last_ingested_at,
                source=source,
                source_metadata=metadata,
            )
            db.add(row)
        else:
            row.coverage_level = coverage_level
            row.update_cadence = update_cadence
            row.last_ingested_at = last_ingested_at
            row.source = source
            row.source_metadata = metadata
            row.updated_at = _now()
        db.flush()
        return row

    def _close_overlapping_windows(
        self,
        db: Session,
        *,
        destination_country: str,
        origin_country: str,
        hs_code: str,
        hs6: str | None,
        national_extension_code: str | None,
        effective_from: datetime,
        preserve_record_id: str | None,
    ) -> int:
        overlapping = (
            db.query(TariffSchedule)
            .filter(
                TariffSchedule.destination_country == destination_country,
                TariffSchedule.origin_country == origin_country,
                or_(TariffSchedule.hs_code == hs_code, TariffSchedule.hs6 == hs6),
                (
                    TariffSchedule.national_extension_code == national_extension_code
                    if national_extension_code is not None
                    else TariffSchedule.national_extension_code.is_(None)
                ),
                TariffSchedule.effective_from < effective_from,
                or_(TariffSchedule.effective_to.is_(None), TariffSchedule.effective_to > effective_from),
            )
            .all()
        )
        changed = 0
        for row in overlapping:
            if preserve_record_id and row.source_record_id == preserve_record_id:
                continue
            row.effective_to = effective_from
            row.updated_at = _now()
            changed += 1
        if changed:
            db.flush()
        return changed

    def ingest_schedule(
        self,
        db: Session,
        *,
        destination_country: str,
        origin_country: str,
        hs_code: str,
        duty_rate_pct: Decimal | str | int | float,
        additional_taxes_pct: Decimal | str | int | float = Decimal("0"),
        effective_from: datetime,
        effective_to: datetime | None = None,
        source: str | None = None,
        confidence: Decimal | str | int | float = Decimal("0.6"),
        hs_version: str | None = None,
        national_extension_code: str | None = None,
        tariff_code_type: str | None = None,
        coverage_level: str | None = None,
        update_cadence: str | None = None,
        fetched_at: datetime | None = None,
        freshness_status: str = "FRESH",
        ttl_seconds: int | None = None,
        provider_id: str | None = None,
        data_source: str | None = None,
        source_record_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> TariffSchedule:
        normalized_destination = _normalize_country(destination_country)
        normalized_origin = _normalize_country(origin_country)
        normalized_hs_code = _normalize_hs_digits(hs_code)
        if not normalized_destination:
            raise ValueError("destination_country is required")
        if not normalized_origin:
            raise ValueError("origin_country is required")
        if not normalized_hs_code or len(normalized_hs_code) < 6:
            raise ValueError("hs_code must contain at least 6 digits")

        normalized_hs6 = _normalize_hs6(normalized_hs_code)
        normalized_extension = _normalize_hs_digits(national_extension_code)
        normalized_fetched_at = fetched_at or _now()
        normalized_coverage_level = str(coverage_level or "partial").strip().lower()
        normalized_tariff_code_type = str(
            tariff_code_type or ("national_extension" if normalized_extension else "HS6")
        )

        payload = {
            "destination_country": normalized_destination,
            "origin_country": normalized_origin,
            "hs_code": normalized_hs_code,
            "hs6": normalized_hs6,
            "hs_version": hs_version,
            "national_extension_code": normalized_extension,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "duty_rate_pct": str(_as_decimal(duty_rate_pct)),
            "additional_taxes_pct": str(_as_decimal(additional_taxes_pct)),
            "source": source,
            "source_record_id": source_record_id,
        }
        record_hash = _hash_payload(payload)
        request_hash = _hash_payload(
            {
                "scope": normalized_destination,
                "payload": payload,
                "coverage_level": normalized_coverage_level,
                "update_cadence": update_cadence,
            }
        )

        run_log = self._get_or_create_run_log(
            db,
            provider=provider_id or data_source or source or "tariff_schedule",
            request_hash=request_hash,
            scope_key=normalized_destination,
        )

        self._upsert_scope_registry(
            db,
            import_country=normalized_destination,
            coverage_level=normalized_coverage_level,
            update_cadence=update_cadence,
            last_ingested_at=normalized_fetched_at,
            source=source,
            source_metadata={
                **(source_metadata or {}),
                "data_source": data_source,
                "provider_id": provider_id,
            },
        )

        row = (
            db.query(TariffSchedule)
            .filter(
                or_(
                    TariffSchedule.source_record_hash == record_hash,
                    and_(
                        TariffSchedule.source_record_id == source_record_id,
                        TariffSchedule.source_record_id.isnot(None),
                    ),
                )
            )
            .first()
        )
        if row is None:
            self._close_overlapping_windows(
                db,
                destination_country=normalized_destination,
                origin_country=normalized_origin,
                hs_code=normalized_hs_code,
                hs6=normalized_hs6,
                national_extension_code=normalized_extension,
                effective_from=effective_from,
                preserve_record_id=source_record_id,
            )
            row = TariffSchedule(
                hs_code=normalized_hs_code,
                hs6=normalized_hs6,
                hs_version=hs_version,
                national_extension_code=normalized_extension,
                tariff_code_type=normalized_tariff_code_type,
                import_country=normalized_destination,
                origin_country=normalized_origin,
                destination_country=normalized_destination,
                coverage_level=normalized_coverage_level,
                duty_rate_pct=_as_decimal(duty_rate_pct),
                additional_taxes_pct=_as_decimal(additional_taxes_pct),
                source=source,
                confidence=_as_decimal(confidence),
                effective_from=effective_from,
                effective_to=effective_to,
                freshness_status=freshness_status,
                ttl_seconds=ttl_seconds or self.DEFAULT_TTL_SECONDS,
                fetched_at=normalized_fetched_at,
                provider_id=provider_id,
                data_source=data_source,
                source_record_id=source_record_id,
                source_record_hash=record_hash,
                source_metadata=dict(source_metadata or {}),
            )
            db.add(row)
            run_log.records_written = (run_log.records_written or 0) + 1
        else:
            run_log.records_skipped = (run_log.records_skipped or 0) + 1

        run_log.freshness_status = freshness_status
        run_log.source_metadata = {
            "destination_country": normalized_destination,
            "origin_country": normalized_origin,
            "hs_code": normalized_hs_code,
            "hs6": normalized_hs6,
            "national_extension_code": normalized_extension,
            "source_record_id": source_record_id,
        }
        run_log.completed_at = _now()
        run_log.updated_at = _now()
        db.flush()
        return row


tariff_ingestion_service = TariffIngestionService()
