"""
Phase 2B Batch 1B: catalog discovery and source SKU linking.

Behavior:
- takes a normalized part identity using canonical_part_key
- reuses existing Phase 2A mappings and existing Phase 2B source links when
  confidence is already high
- calls a Batch 1B connector abstraction only when mappings are missing or weak
- creates/updates canonical_sku rows
- creates/updates source_sku_link rows idempotently
- does not ingest offers/availability
- does not trigger recompute
- does not implement reconciliation or scoring
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.integrations.catalog_connector import (
    CatalogSearchConnector,
    NullCatalogSearchConnector,
)
from app.models.bom import BOMPart
from app.models.canonical import CanonicalSKU, SourceSKULink
from app.models.enrichment import EnrichmentRunLog, PartToSkuMapping
from app.schemas.canonical_catalog import (
    CatalogDiscoveryResult,
    CatalogSearchCandidate,
)
from app.schemas.enrichment import PartIdentity

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


def _first_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _set_first_attr(obj: Any, names: tuple[str, ...], value: Any) -> None:
    for name in names:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return


@contextmanager
def _run_log(
    db: Session,
    *,
    bom_part: BOMPart,
    provider: str,
    idempotency_key: str,
    request_hash: str,
):
    started = _now()
    log = EnrichmentRunLog(
        bom_id=bom_part.bom_id,
        bom_part_id=bom_part.id,
        run_scope="bom_line",
        stage="phase2b_catalog_discovery",
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


class CatalogDiscoveryService:
    STAGE = "phase2b_catalog_discovery"
    MIN_LINK_CONFIDENCE = Decimal("0.50")
    HIGH_CONFIDENCE = Decimal("0.85")

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

    def _query_existing_mappings(
        self,
        db: Session,
        *,
        identity: PartIdentity,
    ) -> list[PartToSkuMapping]:
        filters = []
        if identity.canonical_part_key:
            filters.append(PartToSkuMapping.canonical_part_key == identity.canonical_part_key)
        if identity.manufacturer and identity.normalized_mpn:
            filters.append(
                and_(
                    PartToSkuMapping.manufacturer == identity.manufacturer,
                    PartToSkuMapping.normalized_mpn == identity.normalized_mpn,
                )
            )
        elif identity.normalized_mpn:
            filters.append(PartToSkuMapping.normalized_mpn == identity.normalized_mpn)

        if not filters:
            return []

        return (
            db.query(PartToSkuMapping)
            .filter(or_(*filters))
            .order_by(
                PartToSkuMapping.is_preferred.desc(),
                PartToSkuMapping.confidence.desc(),
                PartToSkuMapping.updated_at.desc(),
            )
            .all()
        )

    def _query_existing_links(
        self,
        db: Session,
        *,
        identity: PartIdentity,
    ) -> list[SourceSKULink]:
        query = (
            db.query(SourceSKULink)
            .join(CanonicalSKU, SourceSKULink.canonical_sku_id == CanonicalSKU.id)
        )

        filters = []
        if hasattr(CanonicalSKU, "canonical_part_key") and identity.canonical_part_key:
            filters.append(CanonicalSKU.canonical_part_key == identity.canonical_part_key)
        if hasattr(CanonicalSKU, "manufacturer") and hasattr(CanonicalSKU, "normalized_mpn"):
            if identity.manufacturer and identity.normalized_mpn:
                filters.append(
                    and_(
                        CanonicalSKU.manufacturer == identity.manufacturer,
                        CanonicalSKU.normalized_mpn == identity.normalized_mpn,
                    )
                )
            elif identity.normalized_mpn:
                filters.append(CanonicalSKU.normalized_mpn == identity.normalized_mpn)

        if not filters:
            return []

        confidence_col = getattr(SourceSKULink, "link_confidence", None) or getattr(SourceSKULink, "confidence")
        updated_col = getattr(SourceSKULink, "updated_at")

        return (
            query.filter(or_(*filters))
            .order_by(confidence_col.desc(), updated_col.desc())
            .all()
        )

    def _link_confidence(self, link: SourceSKULink) -> Decimal:
        return _as_decimal(_first_attr(link, "link_confidence", "confidence", default="0"))

    def _mapping_confidence(self, mapping: PartToSkuMapping) -> Decimal:
        return _as_decimal(mapping.confidence)

    def _is_mapping_high(self, mapping: PartToSkuMapping) -> bool:
        return self._mapping_confidence(mapping) >= self.HIGH_CONFIDENCE

    def _is_link_high(self, link: SourceSKULink) -> bool:
        return self._link_confidence(link) >= self.HIGH_CONFIDENCE

    def _normalize_link_method(self, raw_method: str | None) -> str:
        value = str(raw_method or "").strip().lower()
        if value in {"exact", "connector_exact", "mpn_exact", "exact_match"}:
            return "exact_match"
        if value in {"fuzzy", "fuzzy_text", "text", "description"}:
            return "fuzzy_text"
        if value in {"source_record", "external_id", "id"}:
            return "external_id"
        return "external_id"

    def _score_candidate(
        self,
        *,
        identity: PartIdentity,
        manufacturer: str | None,
        normalized_mpn: str | None,
        link_method: str,
        base_score: Decimal,
    ) -> tuple[Decimal, str]:
        score = base_score
        method = self._normalize_link_method(link_method)

        identity_mpn = (identity.normalized_mpn or "").strip().lower()
        candidate_mpn = (normalized_mpn or "").strip().lower()
        identity_mfr = (identity.manufacturer or "").strip().lower()
        candidate_mfr = (manufacturer or "").strip().lower()

        if identity_mpn and candidate_mpn and identity_mpn == candidate_mpn:
            if not identity_mfr or not candidate_mfr or identity_mfr == candidate_mfr:
                return (max(score, Decimal("0.95")), "exact_match")

        if method == "fuzzy_text":
            return (max(score, Decimal("0.65")), "fuzzy_text")

        if method == "external_id":
            return (max(score, Decimal("0.70")), "external_id")

        return (score, method)

    def _mapping_to_candidate(
        self,
        mapping: PartToSkuMapping,
    ) -> CatalogSearchCandidate:
        external_sku_id = (
            mapping.source_record_id
            or mapping.vendor_sku
            or f"{mapping.source_system}:{mapping.id}"
        )
        link_method = self._normalize_link_method(mapping.match_method)
        score = _as_decimal(mapping.confidence)
        return CatalogSearchCandidate(
            source_system=mapping.source_system or "unknown",
            external_sku_id=str(external_sku_id),
            manufacturer=mapping.manufacturer,
            mpn=mapping.mpn,
            normalized_mpn=mapping.normalized_mpn,
            vendor_sku=mapping.vendor_sku,
            vendor_id=mapping.vendor_id,
            canonical_part_key=mapping.canonical_part_key,
            link_method=link_method,
            link_confidence=score,
            is_ambiguous=False,
            part_to_sku_mapping_id=mapping.id,
            source_metadata=dict(mapping.source_metadata or {}),
        )

    def _upsert_mapping_for_candidate(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        identity: PartIdentity,
        candidate: CatalogSearchCandidate,
    ) -> PartToSkuMapping:
        source_hash = _hash_payload(
            {
                "source_system": candidate.source_system,
                "external_sku_id": candidate.external_sku_id,
                "vendor_id": candidate.vendor_id,
                "vendor_sku": candidate.vendor_sku or candidate.external_sku_id,
                "canonical_part_key": candidate.canonical_part_key or identity.canonical_part_key,
            }
        )

        mapping = None
        if candidate.part_to_sku_mapping_id:
            mapping = (
                db.query(PartToSkuMapping)
                .filter(PartToSkuMapping.id == candidate.part_to_sku_mapping_id)
                .first()
            )

        if mapping is None:
            mapping = (
                db.query(PartToSkuMapping)
                .filter(
                    or_(
                        PartToSkuMapping.source_record_hash == source_hash,
                        and_(
                            PartToSkuMapping.source_system == candidate.source_system,
                            PartToSkuMapping.source_record_id == candidate.external_sku_id,
                        ),
                    )
                )
                .first()
            )

        if mapping is None and candidate.vendor_sku:
            mapping = (
                db.query(PartToSkuMapping)
                .filter(
                    and_(
                        PartToSkuMapping.vendor_id == candidate.vendor_id,
                        PartToSkuMapping.vendor_sku == candidate.vendor_sku,
                    )
                )
                .first()
            )

        metadata = dict(candidate.source_metadata or {})
        metadata.update(
            {
                "mapping_status": "ambiguous" if candidate.is_ambiguous else "resolved",
                "resolved_at": _now().isoformat(),
                "phase": "2B-Batch-1B",
                "link_method": candidate.link_method,
                "link_confidence": str(candidate.link_confidence),
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
                vendor_sku=candidate.vendor_sku or candidate.external_sku_id,
                sku_kind="catalog",
                match_method=candidate.link_method,
                confidence=candidate.link_confidence,
                is_preferred=not candidate.is_ambiguous and candidate.link_confidence >= self.HIGH_CONFIDENCE,
                source_system=candidate.source_system,
                source_record_id=candidate.external_sku_id,
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
            mapping.vendor_sku = candidate.vendor_sku or mapping.vendor_sku or candidate.external_sku_id
            mapping.match_method = candidate.link_method
            mapping.confidence = max(_as_decimal(mapping.confidence), candidate.link_confidence)
            mapping.is_preferred = bool(mapping.is_preferred) or (
                not candidate.is_ambiguous and candidate.link_confidence >= self.HIGH_CONFIDENCE
            )
            mapping.source_system = candidate.source_system
            mapping.source_record_id = candidate.external_sku_id
            mapping.source_record_hash = source_hash
            mapping.source_metadata = metadata
            mapping.updated_at = _now()

        db.flush()
        return mapping

    def _find_or_create_canonical_sku(
        self,
        db: Session,
        *,
        identity: PartIdentity,
        mapping: PartToSkuMapping,
        candidate: CatalogSearchCandidate,
    ) -> CanonicalSKU:
        query = db.query(CanonicalSKU)
        filters = []

        if hasattr(CanonicalSKU, "canonical_part_key"):
            part_key = candidate.canonical_part_key or mapping.canonical_part_key or identity.canonical_part_key
            if part_key:
                filters.append(CanonicalSKU.canonical_part_key == part_key)

        if hasattr(CanonicalSKU, "manufacturer") and hasattr(CanonicalSKU, "normalized_mpn"):
            manufacturer = candidate.manufacturer or mapping.manufacturer or identity.manufacturer
            normalized_mpn = candidate.normalized_mpn or mapping.normalized_mpn or identity.normalized_mpn
            if manufacturer and normalized_mpn:
                filters.append(
                    and_(
                        CanonicalSKU.manufacturer == manufacturer,
                        CanonicalSKU.normalized_mpn == normalized_mpn,
                    )
                )
            elif normalized_mpn:
                filters.append(CanonicalSKU.normalized_mpn == normalized_mpn)

        sku = query.filter(or_(*filters)).order_by(CanonicalSKU.updated_at.desc()).first() if filters else None
        if sku is not None:
            if hasattr(sku, "confidence"):
                sku.confidence = max(_as_decimal(sku.confidence), candidate.link_confidence)
            if hasattr(sku, "updated_at"):
                sku.updated_at = _now()
            db.flush()
            return sku

        manufacturer = candidate.manufacturer or mapping.manufacturer or identity.manufacturer
        mpn = candidate.mpn or mapping.mpn or identity.mpn
        normalized_mpn = candidate.normalized_mpn or mapping.normalized_mpn or identity.normalized_mpn
        canonical_part_key = candidate.canonical_part_key or mapping.canonical_part_key or identity.canonical_part_key
        canonical_key = f"{canonical_part_key or 'canonical'}::{manufacturer or 'unknown'}::{normalized_mpn or mpn or candidate.external_sku_id}"

        payload: dict[str, Any] = {
            "canonical_key": canonical_key,
            "canonical_part_key": canonical_part_key,
            "manufacturer": manufacturer,
            "mpn": mpn,
            "normalized_mpn": normalized_mpn,
        }

        if hasattr(CanonicalSKU, "canonical_name"):
            payload["canonical_name"] = identity.description
        if hasattr(CanonicalSKU, "sku_kind"):
            payload["sku_kind"] = "canonical"
        if hasattr(CanonicalSKU, "status"):
            payload["status"] = "ACTIVE"
        if hasattr(CanonicalSKU, "confidence"):
            payload["confidence"] = candidate.link_confidence
        if hasattr(CanonicalSKU, "consolidation_method"):
            payload["consolidation_method"] = "phase2b_catalog_discovery"
        if hasattr(CanonicalSKU, "review_status"):
            payload["review_status"] = "AMBIGUOUS" if candidate.is_ambiguous else "AUTO"
        if hasattr(CanonicalSKU, "source_metadata"):
            payload["source_metadata"] = {
                "created_from": "phase2b_batch1b",
                "source_system": candidate.source_system,
                "external_sku_id": candidate.external_sku_id,
            }

        sku = CanonicalSKU(**payload)
        db.add(sku)
        db.flush()
        return sku

    def _query_link_by_unique_keys(
        self,
        db: Session,
        *,
        canonical_sku_id: str,
        mapping_id: str | None,
        candidate: CatalogSearchCandidate,
    ) -> SourceSKULink | None:
        filters = [SourceSKULink.canonical_sku_id == canonical_sku_id]

        mapping_col = getattr(SourceSKULink, "part_to_sku_mapping_id", None)
        if mapping_col is not None and mapping_id:
            row = (
                db.query(SourceSKULink)
                .filter(
                    SourceSKULink.canonical_sku_id == canonical_sku_id,
                    mapping_col == mapping_id,
                )
                .first()
            )
            if row is not None:
                return row

        external_col = getattr(SourceSKULink, "external_sku_id", None) or getattr(SourceSKULink, "external_sku_key", None)
        if external_col is not None:
            row = (
                db.query(SourceSKULink)
                .filter(
                    SourceSKULink.canonical_sku_id == canonical_sku_id,
                    SourceSKULink.source_system == candidate.source_system,
                    external_col == candidate.external_sku_id,
                )
                .first()
            )
            if row is not None:
                return row

        return None

    def _upsert_source_link(
        self,
        db: Session,
        *,
        canonical_sku: CanonicalSKU,
        mapping: PartToSkuMapping,
        candidate: CatalogSearchCandidate,
        ambiguous_count: int,
    ) -> SourceSKULink:
        link = self._query_link_by_unique_keys(
            db,
            canonical_sku_id=canonical_sku.id,
            mapping_id=mapping.id,
            candidate=candidate,
        )

        metadata = dict(candidate.source_metadata or {})
        metadata.update(
            {
                "phase": "2B-Batch-1B",
                "ambiguous": candidate.is_ambiguous,
                "ambiguous_match_count": ambiguous_count,
                "canonical_part_key": candidate.canonical_part_key or mapping.canonical_part_key,
                "normalized_mpn": candidate.normalized_mpn or mapping.normalized_mpn,
            }
        )

        if link is None:
            payload: dict[str, Any] = {
                "canonical_sku_id": canonical_sku.id,
                "source_system": candidate.source_system,
            }

            if hasattr(SourceSKULink, "part_to_sku_mapping_id"):
                payload["part_to_sku_mapping_id"] = mapping.id
            if hasattr(SourceSKULink, "vendor_id"):
                payload["vendor_id"] = mapping.vendor_id
            if hasattr(SourceSKULink, "vendor_sku"):
                payload["vendor_sku"] = mapping.vendor_sku
            if hasattr(SourceSKULink, "manufacturer"):
                payload["manufacturer"] = candidate.manufacturer or mapping.manufacturer
            if hasattr(SourceSKULink, "mpn"):
                payload["mpn"] = candidate.mpn or mapping.mpn
            if hasattr(SourceSKULink, "normalized_mpn"):
                payload["normalized_mpn"] = candidate.normalized_mpn or mapping.normalized_mpn
            if hasattr(SourceSKULink, "canonical_part_key"):
                payload["canonical_part_key"] = candidate.canonical_part_key or mapping.canonical_part_key

            external_field = "external_sku_id" if hasattr(SourceSKULink, "external_sku_id") else "external_sku_key"
            payload[external_field] = candidate.external_sku_id

            if hasattr(SourceSKULink, "link_role"):
                payload["link_role"] = "source"
            if hasattr(SourceSKULink, "link_status"):
                payload["link_status"] = "AMBIGUOUS" if candidate.is_ambiguous else "ACTIVE"

            method_field = "link_method" if hasattr(SourceSKULink, "link_method") else "match_method"
            payload[method_field] = candidate.link_method

            confidence_field = "link_confidence" if hasattr(SourceSKULink, "link_confidence") else "confidence"
            payload[confidence_field] = candidate.link_confidence

            if hasattr(SourceSKULink, "is_primary"):
                payload["is_primary"] = (not candidate.is_ambiguous and candidate.link_confidence >= self.HIGH_CONFIDENCE)
            if hasattr(SourceSKULink, "source_metadata"):
                payload["source_metadata"] = metadata

            link = SourceSKULink(**payload)
            db.add(link)
        else:
            if hasattr(link, "part_to_sku_mapping_id") and getattr(link, "part_to_sku_mapping_id", None) is None:
                link.part_to_sku_mapping_id = mapping.id
            if hasattr(link, "vendor_id"):
                link.vendor_id = link.vendor_id or mapping.vendor_id
            if hasattr(link, "vendor_sku"):
                link.vendor_sku = link.vendor_sku or mapping.vendor_sku

            external_field = "external_sku_id" if hasattr(link, "external_sku_id") else "external_sku_key"
            setattr(link, external_field, candidate.external_sku_id)

            method_field = "link_method" if hasattr(link, "link_method") else "match_method"
            setattr(link, method_field, candidate.link_method)

            confidence_field = "link_confidence" if hasattr(link, "link_confidence") else "confidence"
            current_conf = _as_decimal(getattr(link, confidence_field))
            setattr(link, confidence_field, max(current_conf, candidate.link_confidence))

            if hasattr(link, "link_status"):
                link.link_status = "AMBIGUOUS" if candidate.is_ambiguous else "ACTIVE"
            if hasattr(link, "source_metadata"):
                link.source_metadata = metadata
            if hasattr(link, "updated_at"):
                link.updated_at = _now()

        db.flush()
        return link

    def _dedupe_candidates(
        self,
        candidates: list[CatalogSearchCandidate],
    ) -> list[CatalogSearchCandidate]:
        deduped: dict[tuple[str, str], CatalogSearchCandidate] = {}
        for candidate in candidates:
            key = (candidate.source_system, candidate.external_sku_id)
            existing = deduped.get(key)
            if existing is None or candidate.link_confidence > existing.link_confidence:
                deduped[key] = candidate
        return list(deduped.values())

    def _prepare_connector_candidates(
        self,
        *,
        identity: PartIdentity,
        raw_candidates: list[CatalogSearchCandidate],
    ) -> tuple[list[CatalogSearchCandidate], int]:
        accepted: list[CatalogSearchCandidate] = []
        discarded = 0

        for candidate in raw_candidates:
            score, method = self._score_candidate(
                identity=identity,
                manufacturer=candidate.manufacturer,
                normalized_mpn=candidate.normalized_mpn,
                link_method=candidate.link_method,
                base_score=_as_decimal(candidate.link_confidence),
            )
            if score < self.MIN_LINK_CONFIDENCE:
                discarded += 1
                continue

            candidate.link_confidence = score
            candidate.link_method = method
            candidate.canonical_part_key = candidate.canonical_part_key or identity.canonical_part_key
            accepted.append(candidate)

        high_count = sum(1 for c in accepted if c.link_confidence >= self.HIGH_CONFIDENCE)
        if high_count > 1:
            for candidate in accepted:
                if candidate.link_confidence >= self.HIGH_CONFIDENCE:
                    candidate.is_ambiguous = True

        return accepted, discarded

    def resolve_for_bom_part(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        connector: CatalogSearchConnector | None = None,
        trace_id: str | None = None,
    ) -> CatalogDiscoveryResult:
        connector = connector or NullCatalogSearchConnector()
        identity = self.build_identity(bom_part)

        if not identity.canonical_part_key:
            raise ValueError("canonical_part_key is required for Phase 2B Batch 1B discovery")

        request_hash = _hash_payload(
            {
                "bom_part_id": bom_part.id,
                "canonical_part_key": identity.canonical_part_key,
                "manufacturer": identity.manufacturer,
                "normalized_mpn": identity.normalized_mpn,
                "trace_id": trace_id,
            }
        )
        idempotency_key = f"{self.STAGE}:{bom_part.id}:{request_hash}"

        with _run_log(
            db,
            bom_part=bom_part,
            provider=connector.provider_name,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ) as run_log:
            existing_links = self._query_existing_links(db, identity=identity)
            high_links = [row for row in existing_links if self._is_link_high(row)]
            if high_links:
                run_log.records_skipped = len(high_links)
                run_log.source_metadata = {
                    "trace_id": trace_id,
                    "reused_existing_links": True,
                    "existing_link_count": len(existing_links),
                }
                return CatalogDiscoveryResult(
                    canonical_part_key=identity.canonical_part_key,
                    reused_existing_links=True,
                    connector_called=False,
                    ambiguous=len(high_links) > 1,
                    discovered_candidate_count=len(high_links),
                    canonical_sku_ids=list({row.canonical_sku_id for row in high_links}),
                    source_sku_link_ids=[row.id for row in high_links],
                    notes=["reused_high_confidence_source_links"],
                )

            existing_mappings = self._query_existing_mappings(db, identity=identity)
            seed_candidates = [
                self._mapping_to_candidate(mapping)
                for mapping in existing_mappings
                if self._mapping_confidence(mapping) >= self.MIN_LINK_CONFIDENCE
            ]

            connector_called = False
            connector_candidates: list[CatalogSearchCandidate] = []
            discarded = 0

            if not seed_candidates or max(c.link_confidence for c in seed_candidates) < self.HIGH_CONFIDENCE:
                connector_called = True
                raw = connector.search_parts(identity)
                connector_candidates, discarded = self._prepare_connector_candidates(
                    identity=identity,
                    raw_candidates=raw,
                )

            all_candidates = self._dedupe_candidates(seed_candidates + connector_candidates)
            all_candidates.sort(
                key=lambda c: (
                    c.link_confidence < self.HIGH_CONFIDENCE,
                    -float(c.link_confidence),
                    c.source_system,
                    c.external_sku_id,
                )
            )

            high_count = sum(1 for c in all_candidates if c.link_confidence >= self.HIGH_CONFIDENCE)
            if high_count > 1:
                for candidate in all_candidates:
                    if candidate.link_confidence >= self.HIGH_CONFIDENCE:
                        candidate.is_ambiguous = True

            written_links: list[SourceSKULink] = []
            canonical_ids: list[str] = []

            for candidate in all_candidates:
                if candidate.link_confidence < self.MIN_LINK_CONFIDENCE:
                    discarded += 1
                    continue

                mapping = self._upsert_mapping_for_candidate(
                    db,
                    bom_part=bom_part,
                    identity=identity,
                    candidate=candidate,
                )
                canonical_sku = self._find_or_create_canonical_sku(
                    db,
                    identity=identity,
                    mapping=mapping,
                    candidate=candidate,
                )
                link = self._upsert_source_link(
                    db,
                    canonical_sku=canonical_sku,
                    mapping=mapping,
                    candidate=candidate,
                    ambiguous_count=high_count,
                )
                written_links.append(link)
                canonical_ids.append(canonical_sku.id)

            run_log.records_written = len(written_links)
            run_log.records_skipped = len(existing_mappings)
            run_log.source_metadata = {
                "trace_id": trace_id,
                "connector_called": connector_called,
                "seed_candidate_count": len(seed_candidates),
                "connector_candidate_count": len(connector_candidates),
                "discarded_candidate_count": discarded,
                "ambiguous": high_count > 1,
            }

            notes: list[str] = []
            if seed_candidates:
                notes.append("reused_phase2a_mappings")
            if connector_called:
                notes.append("connector_search_performed")
            if high_count > 1:
                notes.append("multiple_high_confidence_matches_marked_ambiguous")
            if discarded:
                notes.append("low_confidence_candidates_discarded")

            return CatalogDiscoveryResult(
                canonical_part_key=identity.canonical_part_key,
                reused_existing_links=False,
                connector_called=connector_called,
                ambiguous=high_count > 1,
                discarded_candidates=discarded,
                discovered_candidate_count=len(all_candidates),
                canonical_sku_ids=list(dict.fromkeys(canonical_ids)),
                source_sku_link_ids=[row.id for row in written_links],
                notes=notes,
            )


catalog_discovery_service = CatalogDiscoveryService()