"""
Phase 2A Batch 2: part-to-SKU mapping pipeline.

Behavior:
- uses normalized part identity from existing Phase 1 persistence on BOMPart
- checks existing part_to_sku_mapping rows first
- calls connector abstraction only when mapping is missing or low-confidence
- writes idempotent mapping rows
- persists run logs in ops.enrichment_run_log
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.integrations.distributor_connector import (
    NullProductDataConnector,
    ProductDataConnector,
)
from app.models.bom import BOMPart
from app.models.enrichment import EnrichmentRunLog, PartToSkuMapping
from app.schemas.enrichment import PartIdentity, ProductSearchCandidate

logger = logging.getLogger(__name__)


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


def _best_score(mapping: PartToSkuMapping) -> Decimal:
    return _as_decimal(mapping.confidence)


def _mapping_status(mapping: PartToSkuMapping) -> str:
    return str((mapping.source_metadata or {}).get("mapping_status") or "resolved")


@contextmanager
def _run_log(
    db: Session,
    *,
    bom_part: BOMPart,
    stage: str,
    provider: str,
    idempotency_key: str,
    request_hash: str,
):
    started = _now()
    log = EnrichmentRunLog(
        bom_id=bom_part.bom_id,
        bom_part_id=bom_part.id,
        run_scope="bom_line",
        stage=stage,
        provider=provider,
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


class PartMappingService:
    STAGE = "part_to_sku_mapping"
    MIN_CONFIDENCE = Decimal("0.75")

    def build_identity(self, bom_part: BOMPart) -> PartIdentity:
        trace = bom_part.normalization_trace_json or {}
        specs = bom_part.specs or {}
        normalized_mpn = (
            trace.get("normalized_mpn")
            or trace.get("part_number_normalized")
            or bom_part.mpn
            or bom_part.part_number
        )
        return PartIdentity(
            bom_part_id=bom_part.id,
            canonical_part_key=bom_part.canonical_part_key,
            manufacturer=bom_part.manufacturer or trace.get("manufacturer"),
            mpn=bom_part.mpn or bom_part.part_number or trace.get("mpn"),
            normalized_mpn=normalized_mpn,
            description=bom_part.description or bom_part.normalized_text or bom_part.raw_text,
            quantity=_as_decimal(bom_part.quantity, "1"),
            unit=bom_part.unit,
            category_code=bom_part.category_code,
            procurement_class=bom_part.procurement_class,
            specs=specs,
            normalization_trace=trace,
        )

    def _query_existing(self, db: Session, identity: PartIdentity) -> list[PartToSkuMapping]:
        query = db.query(PartToSkuMapping)

        filters = []
        if identity.bom_part_id:
            filters.append(PartToSkuMapping.bom_part_id == identity.bom_part_id)
        if identity.canonical_part_key:
            filters.append(PartToSkuMapping.canonical_part_key == identity.canonical_part_key)
        if identity.manufacturer and identity.normalized_mpn:
            filters.append(
                (PartToSkuMapping.manufacturer == identity.manufacturer) &
                (PartToSkuMapping.normalized_mpn == identity.normalized_mpn)
            )
        elif identity.normalized_mpn:
            filters.append(PartToSkuMapping.normalized_mpn == identity.normalized_mpn)

        if not filters:
            return []

        return (
            query.filter(or_(*filters))
            .order_by(
                PartToSkuMapping.is_preferred.desc(),
                PartToSkuMapping.confidence.desc(),
                PartToSkuMapping.updated_at.desc(),
            )
            .all()
        )

    def _should_resolve(self, existing: list[PartToSkuMapping]) -> bool:
        if not existing:
            return True
        best = existing[0]
        if _mapping_status(best) in {"pending", "failed", "needs_review"}:
            return True
        return _best_score(best) < self.MIN_CONFIDENCE

    def _upsert_candidate(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        identity: PartIdentity,
        candidate: ProductSearchCandidate,
    ) -> PartToSkuMapping:
        source_payload = {
            "provider": candidate.source_system,
            "source_record_id": candidate.source_record_id,
            "vendor_id": candidate.vendor_id,
            "vendor_sku": candidate.vendor_sku,
            "canonical_part_key": candidate.canonical_part_key or identity.canonical_part_key,
            "normalized_mpn": candidate.normalized_mpn or identity.normalized_mpn,
        }
        source_hash = _hash_payload(source_payload)

        mapping = (
            db.query(PartToSkuMapping)
            .filter(
                or_(
                    PartToSkuMapping.source_record_hash == source_hash,
                    (
                        (PartToSkuMapping.vendor_id == candidate.vendor_id) &
                        (PartToSkuMapping.vendor_sku == candidate.vendor_sku)
                    ),
                )
            )
            .first()
        )

        metadata = dict(candidate.source_metadata or {})
        metadata.update(
            {
                "mapping_status": candidate.mapping_status,
                "match_score": str(candidate.match_score),
                "resolved_at": _now().isoformat(),
                "normalized_identity": {
                    "canonical_part_key": identity.canonical_part_key,
                    "manufacturer": identity.manufacturer,
                    "mpn": identity.mpn,
                    "normalized_mpn": identity.normalized_mpn,
                },
            }
        )

        if mapping is None:
            mapping = PartToSkuMapping(
                bom_part_id=bom_part.id,
                vendor_id=candidate.vendor_id,
                canonical_part_key=candidate.canonical_part_key or identity.canonical_part_key,
                manufacturer=candidate.manufacturer or identity.manufacturer,
                mpn=candidate.mpn or identity.mpn,
                normalized_mpn=candidate.normalized_mpn or identity.normalized_mpn,
                vendor_sku=candidate.vendor_sku,
                sku_kind="catalog",
                match_method=candidate.match_method,
                confidence=candidate.match_score,
                is_preferred=candidate.is_preferred,
                source_system=candidate.source_system,
                source_record_id=candidate.source_record_id,
                source_record_hash=source_hash,
                source_metadata=metadata,
                valid_from=_now(),
            )
            db.add(mapping)
        else:
            mapping.bom_part_id = mapping.bom_part_id or bom_part.id
            mapping.vendor_id = candidate.vendor_id or mapping.vendor_id
            mapping.canonical_part_key = candidate.canonical_part_key or mapping.canonical_part_key or identity.canonical_part_key
            mapping.manufacturer = candidate.manufacturer or mapping.manufacturer or identity.manufacturer
            mapping.mpn = candidate.mpn or mapping.mpn or identity.mpn
            mapping.normalized_mpn = candidate.normalized_mpn or mapping.normalized_mpn or identity.normalized_mpn
            mapping.match_method = candidate.match_method
            mapping.confidence = candidate.match_score
            mapping.is_preferred = candidate.is_preferred
            mapping.source_system = candidate.source_system
            mapping.source_record_id = candidate.source_record_id
            mapping.source_record_hash = source_hash
            mapping.source_metadata = metadata
            mapping.updated_at = _now()

        db.flush()
        return mapping

    def resolve_for_bom_part(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        connector: ProductDataConnector | None = None,
        trace_id: str | None = None,
    ) -> list[PartToSkuMapping]:
        connector = connector or NullProductDataConnector()
        identity = self.build_identity(bom_part)
        existing = self._query_existing(db, identity)
        if not self._should_resolve(existing):
            return existing

        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part.id,
                "canonical_part_key": identity.canonical_part_key,
                "manufacturer": identity.manufacturer,
                "mpn": identity.mpn,
                "normalized_mpn": identity.normalized_mpn,
                "trace_id": trace_id,
            }
        )
        idempotency_key = f"{self.STAGE}:{bom_part.id}:{request_hash}"

        with _run_log(
            db,
            bom_part=bom_part,
            stage=self.STAGE,
            provider=connector.provider_name,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ) as run_log:
            candidates = connector.search_products(identity)
            if not candidates:
                run_log.records_skipped = len(existing)
                run_log.source_metadata = {"reason": "no_candidates"}
                if existing:
                    return existing

                pending = PartToSkuMapping(
                    bom_part_id=bom_part.id,
                    vendor_id=None,
                    canonical_part_key=identity.canonical_part_key,
                    manufacturer=identity.manufacturer,
                    mpn=identity.mpn,
                    normalized_mpn=identity.normalized_mpn,
                    vendor_sku=f"UNRESOLVED:{bom_part.id}",
                    sku_kind="catalog",
                    match_method="connector_none",
                    confidence=Decimal("0"),
                    is_preferred=False,
                    source_system=connector.provider_name,
                    source_record_id=None,
                    source_record_hash=request_hash,
                    source_metadata={
                        "mapping_status": "pending",
                        "match_score": "0",
                        "trace_id": trace_id,
                    },
                    valid_from=_now(),
                )
                db.add(pending)
                db.flush()
                run_log.records_written = 1
                return [pending]

            rows: list[PartToSkuMapping] = []
            for candidate in candidates:
                rows.append(
                    self._upsert_candidate(
                        db,
                        bom_part=bom_part,
                        identity=identity,
                        candidate=candidate,
                    )
                )

            rows.sort(
                key=lambda row: (
                    not bool(row.is_preferred),
                    -float(_best_score(row)),
                    row.updated_at.timestamp() if row.updated_at else 0,
                )
            )

            run_log.records_written = len(rows)
            run_log.records_skipped = max(len(existing) - len(rows), 0)
            run_log.source_metadata = {
                "candidate_count": len(candidates),
                "trace_id": trace_id,
            }
            return rows


part_mapping_service = PartMappingService()