"""
Phase 2A Batch 3 - Part 1: HS mapping lookup service.

Behavior:
- resolves HS mapping for a BOM line from existing market.hs_mapping rows
- checks direct BOM line mapping first, then canonical part key, then minimal taxonomy
- returns explicit uncertainty for low-confidence or missing mappings
- does not invent or persist new HS mappings
- persists run logs in ops.enrichment_run_log for observability
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.enrichment import EnrichmentRunLog, HSMapping
from app.schemas.enrichment import HSResolutionDTO


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


@contextmanager
def _run_log(
    db: Session,
    *,
    bom_part: BOMPart,
    idempotency_key: str,
    request_hash: str,
):
    started = _now()
    log = EnrichmentRunLog(
        bom_id=bom_part.bom_id,
        bom_part_id=bom_part.id,
        run_scope="bom_line",
        stage="hs_mapping_lookup",
        provider="hs_mapping",
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


class HSMappingService:
    STAGE = "hs_mapping_lookup"
    MIN_CONFIDENCE = Decimal("0.75")

    def _query_candidates(self, db: Session, bom_part: BOMPart) -> list[tuple[HSMapping, str, int]]:
        candidates: list[tuple[HSMapping, str, int]] = []
        seen_ids: set[str] = set()

        def _append(rows: list[HSMapping], matched_on: str, priority: int) -> None:
            for row in rows:
                if row.id in seen_ids:
                    continue
                seen_ids.add(row.id)
                candidates.append((row, matched_on, priority))

        direct_rows = (
            db.query(HSMapping)
            .filter(HSMapping.bom_part_id == bom_part.id)
            .order_by(HSMapping.confidence.desc(), HSMapping.updated_at.desc())
            .all()
        )
        _append(direct_rows, "bom_part_id", 0)

        if bom_part.canonical_part_key:
            canonical_rows = (
                db.query(HSMapping)
                .filter(
                    HSMapping.bom_part_id.is_(None),
                    HSMapping.canonical_part_key == bom_part.canonical_part_key,
                )
                .order_by(HSMapping.confidence.desc(), HSMapping.updated_at.desc())
                .all()
            )
            _append(canonical_rows, "canonical_part_key", 1)

        taxonomy_filters = []
        if bom_part.category_code:
            taxonomy_filters.append(HSMapping.category_code == bom_part.category_code)
        if bom_part.material:
            taxonomy_filters.append(HSMapping.material == bom_part.material)

        if taxonomy_filters:
            taxonomy_rows = (
                db.query(HSMapping)
                .filter(
                    HSMapping.bom_part_id.is_(None),
                    HSMapping.canonical_part_key.is_(None),
                    and_(*taxonomy_filters),
                )
                .order_by(HSMapping.confidence.desc(), HSMapping.updated_at.desc())
                .all()
            )
            if taxonomy_rows:
                matched_on = "category_material" if bom_part.category_code and bom_part.material else "taxonomy"
                _append(taxonomy_rows, matched_on, 2)

        candidates.sort(
            key=lambda item: (
                item[2],
                -float(_as_decimal(item[0].confidence)),
                0 if str(item[0].review_status or "AUTO").upper() == "APPROVED" else 1,
                item[0].updated_at.timestamp() if item[0].updated_at else 0,
            )
        )
        return candidates

    def _to_result(
        self,
        *,
        bom_part: BOMPart,
        row: HSMapping,
        matched_on: str,
    ) -> HSResolutionDTO:
        confidence = _as_decimal(row.confidence)
        review_status = str(row.review_status or "AUTO")
        is_resolved = bool(row.hs_code) and confidence >= self.MIN_CONFIDENCE
        uncertainty_reason = None if is_resolved else "low_confidence_mapping"
        resolution_status = "resolved" if is_resolved else "needs_review"

        metadata = dict(row.source_metadata or {})
        metadata.setdefault("matched_on", matched_on)

        return HSResolutionDTO(
            bom_part_id=bom_part.id,
            resolution_status=resolution_status,
            resolved=is_resolved,
            hs_code=row.hs_code,
            hs_version=row.hs_version,
            jurisdiction=row.jurisdiction,
            confidence=confidence,
            mapping_method=row.mapping_method,
            review_status=review_status,
            matched_on=matched_on,
            source_system=row.source_system,
            source_record_id=row.source_record_id,
            source_metadata=metadata,
            uncertainty_reason=uncertainty_reason,
            mapping_id=row.id,
        )

    def _missing_result(self, bom_part: BOMPart) -> HSResolutionDTO:
        return HSResolutionDTO(
            bom_part_id=bom_part.id,
            resolution_status="unresolved",
            resolved=False,
            confidence=Decimal("0"),
            matched_on=None,
            uncertainty_reason="no_hs_mapping_found",
            source_metadata={
                "canonical_part_key": bom_part.canonical_part_key,
                "category_code": bom_part.category_code,
                "material": bom_part.material,
            },
        )

    def resolve_for_bom_part(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        trace_id: str | None = None,
    ) -> HSResolutionDTO:
        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part.id,
                "canonical_part_key": bom_part.canonical_part_key,
                "category_code": bom_part.category_code,
                "material": bom_part.material,
                "trace_id": trace_id,
            }
        )
        idempotency_key = f"{self.STAGE}:{bom_part.id}:{request_hash}"

        with _run_log(
            db,
            bom_part=bom_part,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ) as run_log:
            candidates = self._query_candidates(db, bom_part)
            if not candidates:
                result = self._missing_result(bom_part)
                run_log.records_skipped = 1
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "resolution_status": result.resolution_status,
                    "uncertainty_reason": result.uncertainty_reason,
                }
                return result

            row, matched_on, _priority = candidates[0]
            result = self._to_result(bom_part=bom_part, row=row, matched_on=matched_on)
            run_log.records_written = 1 if result.resolved else 0
            run_log.records_skipped = 0 if result.resolved else 1
            run_log.freshness_status = "REFERENCE"
            run_log.source_metadata = {
                "trace_id": trace_id,
                "mapping_id": row.id,
                "matched_on": matched_on,
                "resolution_status": result.resolution_status,
                "uncertainty_reason": result.uncertainty_reason,
            }
            return result


hs_mapping_service = HSMappingService()