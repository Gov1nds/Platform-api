from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.enums import BOMLineStatus, ProjectStatus
from app.models.bom import BOM, BOMPart
from app.models.enrichment import (
    BOMLineDependencyIndex,
    EnrichmentRunLog,
    PartToSkuMapping,
    SKUAvailabilitySnapshot,
    SKUOffer,
)
from app.models.market import TariffSchedule
from app.models.project import Project
from app.services.enrichment.phase2a_evidence_service import phase2a_evidence_service
from app.services.runtime_pipeline import runtime_pipeline_service

logger = logging.getLogger(__name__)


class Phase2ARecomputeService:
    """
    Batch 5 recomputation logic.

    Responsibilities:
    - derive dependency rows from existing Phase 2A evidence
    - map upstream dataset changes to affected BOM lines
    - coalesce queueing to avoid recompute storms
    - rerun Phase 2A enrichment + scoring for a single affected line only
    - mark active lines for refresh when evidence TTL has expired
    """

    DEPENDENCY_TYPES = {"external_sku", "hs6", "lane_key", "dataset"}
    ENQUEUE_STAGE = "phase2a_recompute_enqueue"
    EXECUTE_STAGE = "phase2a_recompute_execute"
    COALESCE_WINDOW_SECONDS = 45
    MAX_RECOMPUTES_PER_MINUTE = 120
    TERMINAL_LINE_STATUSES = {
        BOMLineStatus.DELIVERED,
        BOMLineStatus.CLOSED,
        BOMLineStatus.CANCELLED,
    }
    ACTIVE_PROJECT_STATUSES = {
        ProjectStatus.DRAFT,
        ProjectStatus.INTAKE_COMPLETE,
        ProjectStatus.ANALYSIS_IN_PROGRESS,
        ProjectStatus.ANALYSIS_COMPLETE,
        ProjectStatus.SOURCING_ACTIVE,
        ProjectStatus.ORDERING_IN_PROGRESS,
        ProjectStatus.EXECUTION_ACTIVE,
        ProjectStatus.PARTIALLY_DELIVERED,
    }

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _hash(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _window_bucket(self, now: datetime | None = None) -> str:
        current = now or self._now()
        bucket = int(current.timestamp() // self.COALESCE_WINDOW_SECONDS)
        return str(bucket)

    def _dependency_row_hash(self, *, bom_part_id: str, dependency_type: str, value: str) -> str:
        return self._hash(
            {
                "bom_part_id": bom_part_id,
                "dependency_type": dependency_type,
                "value": value,
            }
        )

    def _extract_dependency_payload(
        self,
        *,
        bom_part: BOMPart,
        phase2a_bundle: dict[str, Any] | None,
    ) -> dict[str, list[str]]:
        bundle = phase2a_bundle or {}
        offer = bundle.get("offer_evidence") or {}
        tariff = bundle.get("tariff_evidence") or {}
        freight = bundle.get("freight_evidence") or {}

        external_sku_ids: set[str] = set()
        for key in (
            offer.get("vendor_sku"),
            offer.get("selected_mapping_id"),
            offer.get("selected_offer_id"),
            (offer.get("source_metadata") or {}).get("external_sku_id"),
            (offer.get("source_metadata") or {}).get("vendor_sku"),
        ):
            if key:
                external_sku_ids.add(str(key))

        hs_code = str(tariff.get("hs_code") or "").strip()
        hs6_values = [hs_code[:6]] if len(hs_code) >= 6 else []

        lane_key_parts = [
            freight.get("origin_country"),
            freight.get("origin_region"),
            freight.get("destination_country"),
            freight.get("destination_region"),
            freight.get("mode"),
        ]
        lane_key = "|".join(str(v or "").strip().upper() for v in lane_key_parts)
        lane_keys = [lane_key] if lane_key.replace("|", "") else []

        datasets_used: set[str] = set()
        if offer.get("selected_mapping_id"):
            datasets_used.add("part_to_sku_mapping")
        if offer.get("selected_offer_id"):
            datasets_used.add("sku_offers")
        if (bundle.get("availability_evidence") or {}).get("snapshot_id"):
            datasets_used.add("sku_availability_snapshots")
        if tariff.get("tariff_schedule_id"):
            datasets_used.add("tariff_schedules")
        if freight.get("lane_rate_band_id"):
            datasets_used.add("lane_rate_bands")
        if tariff.get("hs_code"):
            datasets_used.add("hs_mapping")

        return {
            "external_sku_ids": sorted(external_sku_ids),
            "hs6": hs6_values,
            "lane_key": lane_keys,
            "datasets_used": sorted(datasets_used),
        }

    def rebuild_dependency_index_for_bom_line(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        phase2a_bundle: dict[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        dependency_payload = self._extract_dependency_payload(
            bom_part=bom_part,
            phase2a_bundle=phase2a_bundle or (bom_part.enrichment_json or {}).get("phase2a"),
        )

        existing_rows = (
            db.query(BOMLineDependencyIndex)
            .filter(
                BOMLineDependencyIndex.bom_id == bom_part.bom_id,
                BOMLineDependencyIndex.parent_bom_part_id == bom_part.id,
                BOMLineDependencyIndex.child_bom_part_id == bom_part.id,
                BOMLineDependencyIndex.dependency_type.in_(tuple(self.DEPENDENCY_TYPES)),
            )
            .all()
        )
        for row in existing_rows:
            db.delete(row)
        db.flush()

        rows_to_add: list[BOMLineDependencyIndex] = []
        for external_sku_id in dependency_payload["external_sku_ids"]:
            rows_to_add.append(
                BOMLineDependencyIndex(
                    bom_id=bom_part.bom_id,
                    parent_bom_part_id=bom_part.id,
                    child_bom_part_id=bom_part.id,
                    dependency_type="external_sku",
                    dependency_strength=1,
                    dependency_metadata={"external_sku_id": external_sku_id},
                    source_system="platform-api",
                    source_record_id=bom_part.id,
                    source_record_hash=self._dependency_row_hash(
                        bom_part_id=bom_part.id,
                        dependency_type="external_sku",
                        value=external_sku_id,
                    ),
                )
            )

        for hs6 in dependency_payload["hs6"]:
            rows_to_add.append(
                BOMLineDependencyIndex(
                    bom_id=bom_part.bom_id,
                    parent_bom_part_id=bom_part.id,
                    child_bom_part_id=bom_part.id,
                    dependency_type="hs6",
                    dependency_strength=1,
                    dependency_metadata={"hs6": hs6},
                    source_system="platform-api",
                    source_record_id=bom_part.id,
                    source_record_hash=self._dependency_row_hash(
                        bom_part_id=bom_part.id,
                        dependency_type="hs6",
                        value=hs6,
                    ),
                )
            )

        for lane_key in dependency_payload["lane_key"]:
            rows_to_add.append(
                BOMLineDependencyIndex(
                    bom_id=bom_part.bom_id,
                    parent_bom_part_id=bom_part.id,
                    child_bom_part_id=bom_part.id,
                    dependency_type="lane_key",
                    dependency_strength=1,
                    dependency_metadata={"lane_key": lane_key},
                    source_system="platform-api",
                    source_record_id=bom_part.id,
                    source_record_hash=self._dependency_row_hash(
                        bom_part_id=bom_part.id,
                        dependency_type="lane_key",
                        value=lane_key,
                    ),
                )
            )

        for dataset_name in dependency_payload["datasets_used"]:
            rows_to_add.append(
                BOMLineDependencyIndex(
                    bom_id=bom_part.bom_id,
                    parent_bom_part_id=bom_part.id,
                    child_bom_part_id=bom_part.id,
                    dependency_type="dataset",
                    dependency_strength=1,
                    dependency_metadata={"dataset": dataset_name},
                    source_system="platform-api",
                    source_record_id=bom_part.id,
                    source_record_hash=self._dependency_row_hash(
                        bom_part_id=bom_part.id,
                        dependency_type="dataset",
                        value=dataset_name,
                    ),
                )
            )

        for row in rows_to_add:
            db.add(row)
        db.flush()
        return dependency_payload

    def _active_line_query(self, db: Session):
        return (
            db.query(BOMPart)
            .join(BOM, BOM.id == BOMPart.bom_id)
            .outerjoin(Project, Project.id == BOM.project_id)
            .filter(
                BOMPart.deleted_at.is_(None),
                BOM.deleted_at.is_(None),
                ~BOMPart.status.in_(tuple(self.TERMINAL_LINE_STATUSES)),
                or_(
                    BOM.project_id.is_(None),
                    and_(
                        Project.deleted_at.is_(None),
                        Project.status.in_(tuple(self.ACTIVE_PROJECT_STATUSES)),
                    ),
                ),
            )
        )

    def _affected_line_ids_from_dependencies(
        self,
        db: Session,
        *,
        external_sku_ids: Iterable[str] | None = None,
        hs6_values: Iterable[str] | None = None,
        lane_keys: Iterable[str] | None = None,
        datasets: Iterable[str] | None = None,
        active_only: bool = True,
    ) -> list[str]:
        external_sku_ids = [str(v) for v in (external_sku_ids or []) if v]
        hs6_values = [str(v)[:6] for v in (hs6_values or []) if v]
        lane_keys = [str(v) for v in (lane_keys or []) if v]
        datasets = [str(v) for v in (datasets or []) if v]

        dependency_filters = []
        if external_sku_ids:
            dependency_filters.append(
                and_(
                    BOMLineDependencyIndex.dependency_type == "external_sku",
                    BOMLineDependencyIndex.dependency_metadata["external_sku_id"].astext.in_(external_sku_ids),
                )
            )
        if hs6_values:
            dependency_filters.append(
                and_(
                    BOMLineDependencyIndex.dependency_type == "hs6",
                    BOMLineDependencyIndex.dependency_metadata["hs6"].astext.in_(hs6_values),
                )
            )
        if lane_keys:
            dependency_filters.append(
                and_(
                    BOMLineDependencyIndex.dependency_type == "lane_key",
                    BOMLineDependencyIndex.dependency_metadata["lane_key"].astext.in_(lane_keys),
                )
            )
        if datasets:
            dependency_filters.append(
                and_(
                    BOMLineDependencyIndex.dependency_type == "dataset",
                    BOMLineDependencyIndex.dependency_metadata["dataset"].astext.in_(datasets),
                )
            )

        if not dependency_filters:
            return []

        query = db.query(BOMLineDependencyIndex.child_bom_part_id).filter(or_(*dependency_filters))
        if active_only:
            query = (
                query.join(BOMPart, BOMPart.id == BOMLineDependencyIndex.child_bom_part_id)
                .join(BOM, BOM.id == BOMPart.bom_id)
                .outerjoin(Project, Project.id == BOM.project_id)
                .filter(
                    BOMPart.deleted_at.is_(None),
                    BOM.deleted_at.is_(None),
                    ~BOMPart.status.in_(tuple(self.TERMINAL_LINE_STATUSES)),
                    or_(
                        BOM.project_id.is_(None),
                        and_(
                            Project.deleted_at.is_(None),
                            Project.status.in_(tuple(self.ACTIVE_PROJECT_STATUSES)),
                        ),
                    ),
                )
            )

        return sorted({row[0] for row in query.all() if row and row[0]})

    def _recent_recompute_count(self, db: Session, *, now: datetime) -> int:
        window_start = now - timedelta(minutes=1)
        return (
            db.query(EnrichmentRunLog)
            .filter(
                EnrichmentRunLog.stage == self.ENQUEUE_STAGE,
                EnrichmentRunLog.started_at >= window_start,
                EnrichmentRunLog.status.in_(["queued", "dispatched"]),
            )
            .count()
        )

    def _merge_reasons(self, metadata: dict[str, Any], reason: str | None, dataset: str | None) -> dict[str, Any]:
        merged = dict(metadata or {})
        reasons = set(merged.get("reasons") or [])
        datasets = set(merged.get("datasets") or [])
        if reason:
            reasons.add(reason)
        if dataset:
            datasets.add(dataset)
        merged["reasons"] = sorted(reasons)
        merged["datasets"] = sorted(datasets)
        return merged

    def enqueue_recompute_for_bom_lines(
        self,
        db: Session,
        *,
        bom_line_ids: Iterable[str],
        reason: str,
        dataset: str,
        source_keys: dict[str, Any] | None = None,
        coalesce_window_seconds: int | None = None,
    ) -> dict[str, Any]:
        ids = [str(v) for v in bom_line_ids if v]
        if not ids:
            return {"enqueued": 0, "skipped": 0, "reason": reason}

        now = self._now()
        bucket = self._window_bucket(now)
        countdown = int(coalesce_window_seconds or self.COALESCE_WINDOW_SECONDS)
        current_rate = self._recent_recompute_count(db, now=now)
        enqueued = 0
        skipped = 0

        from app.workers.pipeline import task_recompute_bom_line_phase2a

        for bom_line_id in sorted(set(ids)):
            if current_rate >= self.MAX_RECOMPUTES_PER_MINUTE:
                skipped += 1
                continue

            idempotency_key = f"{self.ENQUEUE_STAGE}:{bom_line_id}:{bucket}"
            existing = (
                db.query(EnrichmentRunLog)
                .filter(EnrichmentRunLog.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                existing.source_metadata = self._merge_reasons(existing.source_metadata or {}, reason, dataset)
                extra = dict(existing.source_metadata or {})
                extra["source_keys"] = source_keys or {}
                existing.source_metadata = extra
                existing.updated_at = now
                skipped += 1
                continue

            log = EnrichmentRunLog(
                bom_part_id=bom_line_id,
                run_scope="bom_line",
                stage=self.ENQUEUE_STAGE,
                provider="phase2a-recompute",
                status="queued",
                idempotency_key=idempotency_key,
                attempt_count=1,
                source_system="platform-api",
                source_metadata={
                    "bucket": bucket,
                    "reasons": [reason],
                    "datasets": [dataset],
                    "source_keys": source_keys or {},
                    "countdown_seconds": countdown,
                },
                started_at=now,
            )
            db.add(log)
            db.flush()

            task_recompute_bom_line_phase2a.apply_async(
                kwargs={
                    "bom_line_id": bom_line_id,
                    "trigger_reason": reason,
                    "dataset": dataset,
                    "source_keys": source_keys or {},
                },
                countdown=countdown,
            )
            log.status = "dispatched"
            current_rate += 1
            enqueued += 1

        db.flush()
        return {"enqueued": enqueued, "skipped": skipped, "reason": reason, "dataset": dataset}

    def trigger_for_sku_offer_change(self, db: Session, *, sku_offer_id: str, reason: str = "sku_offer_changed") -> dict[str, Any]:
        offer = db.query(SKUOffer).filter(SKUOffer.id == sku_offer_id).first()
        if not offer:
            return {"enqueued": 0, "skipped": 0, "reason": reason, "dataset": "sku_offers"}
        mapping = db.query(PartToSkuMapping).filter(PartToSkuMapping.id == offer.part_to_sku_mapping_id).first()
        external_keys = [offer.id]
        if mapping and mapping.vendor_sku:
            external_keys.append(mapping.vendor_sku)
        line_ids = self._affected_line_ids_from_dependencies(
            db,
            external_sku_ids=external_keys,
            datasets=["sku_offers", "part_to_sku_mapping"],
        )
        return self.enqueue_recompute_for_bom_lines(
            db,
            bom_line_ids=line_ids,
            reason=reason,
            dataset="sku_offers",
            source_keys={"sku_offer_id": sku_offer_id, "external_sku_ids": external_keys},
        )

    def trigger_for_availability_change(
        self,
        db: Session,
        *,
        sku_offer_id: str,
        reason: str = "sku_availability_changed",
    ) -> dict[str, Any]:
        offer = db.query(SKUOffer).filter(SKUOffer.id == sku_offer_id).first()
        if not offer:
            return {"enqueued": 0, "skipped": 0, "reason": reason, "dataset": "sku_availability_snapshots"}
        mapping = db.query(PartToSkuMapping).filter(PartToSkuMapping.id == offer.part_to_sku_mapping_id).first()
        external_keys = [offer.id]
        if mapping and mapping.vendor_sku:
            external_keys.append(mapping.vendor_sku)
        line_ids = self._affected_line_ids_from_dependencies(
            db,
            external_sku_ids=external_keys,
            datasets=["sku_availability_snapshots"],
        )
        return self.enqueue_recompute_for_bom_lines(
            db,
            bom_line_ids=line_ids,
            reason=reason,
            dataset="sku_availability_snapshots",
            source_keys={"sku_offer_id": sku_offer_id, "external_sku_ids": external_keys},
        )

    def trigger_for_tariff_change(
        self,
        db: Session,
        *,
        tariff_schedule_id: str | None = None,
        hs_code: str | None = None,
        reason: str = "tariff_schedule_changed",
    ) -> dict[str, Any]:
        hs_probe = hs_code
        if tariff_schedule_id and not hs_probe:
            row = db.query(TariffSchedule).filter(TariffSchedule.id == tariff_schedule_id).first()
            if row:
                hs_probe = row.hs_code
        hs6 = str(hs_probe or "")[:6]
        if not hs6:
            return {"enqueued": 0, "skipped": 0, "reason": reason, "dataset": "tariff_schedules"}
        line_ids = self._affected_line_ids_from_dependencies(
            db,
            hs6_values=[hs6],
            datasets=["tariff_schedules", "hs_mapping"],
        )
        return self.enqueue_recompute_for_bom_lines(
            db,
            bom_line_ids=line_ids,
            reason=reason,
            dataset="tariff_schedules",
            source_keys={"tariff_schedule_id": tariff_schedule_id, "hs6": hs6},
        )

    def trigger_for_lane_rate_change(
        self,
        db: Session,
        *,
        lane_key: str,
        reason: str = "lane_rate_band_changed",
    ) -> dict[str, Any]:
        line_ids = self._affected_line_ids_from_dependencies(
            db,
            lane_keys=[lane_key],
            datasets=["lane_rate_bands"],
        )
        return self.enqueue_recompute_for_bom_lines(
            db,
            bom_line_ids=line_ids,
            reason=reason,
            dataset="lane_rate_bands",
            source_keys={"lane_key": lane_key},
        )

    def trigger_for_mapping_change(
        self,
        db: Session,
        *,
        mapping_id: str,
        reason: str = "part_mapping_changed",
    ) -> dict[str, Any]:
        mapping = db.query(PartToSkuMapping).filter(PartToSkuMapping.id == mapping_id).first()
        if not mapping:
            return {"enqueued": 0, "skipped": 0, "reason": reason, "dataset": "part_to_sku_mapping"}
        external_keys = [mapping.id]
        if mapping.vendor_sku:
            external_keys.append(mapping.vendor_sku)
        line_ids = self._affected_line_ids_from_dependencies(
            db,
            external_sku_ids=external_keys,
            datasets=["part_to_sku_mapping"],
        )
        if mapping.bom_part_id:
            line_ids = sorted(set(line_ids + [mapping.bom_part_id]))
        return self.enqueue_recompute_for_bom_lines(
            db,
            bom_line_ids=line_ids,
            reason=reason,
            dataset="part_to_sku_mapping",
            source_keys={"mapping_id": mapping_id, "external_sku_ids": external_keys},
        )

    def _snapshot_ttl_expired(self, snapshot: SKUAvailabilitySnapshot | None) -> bool:
        if snapshot is None or snapshot.snapshot_at is None:
            return True
        ttl_seconds = None
        metadata = snapshot.source_metadata or {}
        if metadata.get("ttl_seconds") is not None:
            try:
                ttl_seconds = int(metadata.get("ttl_seconds"))
            except Exception:
                ttl_seconds = None
        if ttl_seconds is None:
            ttl_seconds = 900
        return snapshot.snapshot_at + timedelta(seconds=ttl_seconds) <= self._now()

    def _line_needs_refresh(self, db: Session, *, bom_part: BOMPart) -> tuple[bool, list[str]]:
        bundle = (bom_part.enrichment_json or {}).get("phase2a") or {}
        reasons: list[str] = []

        offer_id = (bundle.get("offer_evidence") or {}).get("selected_offer_id")
        if offer_id:
            offer = db.query(SKUOffer).filter(SKUOffer.id == offer_id).first()
            if offer is None:
                reasons.append("offer_missing")
            elif offer.valid_to and offer.valid_to <= self._now():
                reasons.append("offer_expired")
            else:
                latest_snapshot = (
                    db.query(SKUAvailabilitySnapshot)
                    .filter(SKUAvailabilitySnapshot.sku_offer_id == offer_id)
                    .order_by(SKUAvailabilitySnapshot.snapshot_at.desc(), SKUAvailabilitySnapshot.created_at.desc())
                    .first()
                )
                if self._snapshot_ttl_expired(latest_snapshot):
                    reasons.append("availability_expired")
        else:
            reasons.append("offer_missing")

        freshness_summary = bundle.get("freshness_summary") or {}
        for key in ("status", "offer_status", "availability_status", "tariff_status", "freight_status"):
            status = str(freshness_summary.get(key) or "").lower()
            if status == "expired":
                reasons.append(f"phase2a_{key}_expired")
            elif key == "status" and status in {"stale", "mixed"}:
                reasons.append(f"phase2a_{key}_stale")

        return (len(reasons) > 0, sorted(set(reasons)))

    def mark_stale_active_lines_for_refresh(self, db: Session) -> dict[str, Any]:
        queued = 0
        marked = 0
        scanned = 0

        active_lines = self._active_line_query(db).all()
        for line in active_lines:
            scanned += 1
            needs_refresh, reasons = self._line_needs_refresh(db, bom_part=line)
            if not needs_refresh:
                continue

            freshness_json = dict(line.data_freshness_json or {})
            freshness_json["phase2a_refresh_needed"] = True
            freshness_json["phase2a_refresh_reasons"] = reasons
            freshness_json["phase2a_refresh_marked_at"] = self._now().isoformat()
            line.data_freshness_json = freshness_json
            marked += 1

            result = self.enqueue_recompute_for_bom_lines(
                db,
                bom_line_ids=[line.id],
                reason="phase2a_ttl_refresh",
                dataset="phase2a_refresh",
                source_keys={"reasons": reasons},
            )
            queued += int(result.get("enqueued", 0))

        db.flush()
        return {"scanned": scanned, "marked": marked, "enqueued": queued}

    def recompute_bom_line(
        self,
        db: Session,
        *,
        bom_line_id: str,
        trigger_reason: str | None = None,
        dataset: str | None = None,
        source_keys: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        part = (
            self._active_line_query(db)
            .filter(BOMPart.id == bom_line_id)
            .first()
        )
        if not part:
            return {"status": "skipped", "bom_line_id": bom_line_id, "reason": "inactive_or_missing"}

        bom = db.query(BOM).filter(BOM.id == part.bom_id).first()
        project = db.query(Project).filter(Project.id == bom.project_id).first() if bom and bom.project_id else None
        target_currency = (bom.target_currency if bom else None) or "USD"
        delivery_location = (bom.delivery_location if bom else None) or ""
        weight_profile = (project.weight_profile if project else None) or "balanced"

        execution_log = EnrichmentRunLog(
            bom_id=bom.id if bom else None,
            bom_part_id=part.id,
            project_id=project.id if project else None,
            run_scope="bom_line",
            stage=self.EXECUTE_STAGE,
            provider="phase2a-recompute",
            status="started",
            idempotency_key=f"{self.EXECUTE_STAGE}:{bom_line_id}:{self._now().isoformat()}",
            attempt_count=1,
            source_system="platform-api",
            source_metadata={
                "trigger_reason": trigger_reason,
                "dataset": dataset,
                "source_keys": source_keys or {},
            },
            started_at=self._now(),
        )
        db.add(execution_log)
        db.flush()

        bundle = phase2a_evidence_service.assemble_for_bom_part(
            db,
            bom_part=part,
            bom=bom,
            project=project,
            target_currency=target_currency,
            trace_id=f"phase2a-recompute:{part.id}",
        )
        phase2a_dict = {
            "bom_part_id": bundle.bom_part_id,
            "offer_evidence": bundle.offer_evidence,
            "availability_evidence": bundle.availability_evidence,
            "tariff_evidence": bundle.tariff_evidence,
            "freight_evidence": bundle.freight_evidence,
            "freshness_summary": bundle.freshness_summary,
            "confidence_summary": bundle.confidence_summary,
            "uncertainty_flags": bundle.uncertainty_flags,
            "notes": bundle.notes,
        }
        dependency_payload = self.rebuild_dependency_index_for_bom_line(
            db,
            bom_part=part,
            phase2a_bundle=phase2a_dict,
        )

        normalized = {
            "bom_part_id": part.id,
            "row_number": part.row_number,
            "canonical_part_key": part.canonical_part_key,
            "normalized_text": part.normalized_text,
            "procurement_class": part.procurement_class,
            "material": part.material,
            "quantity": float(part.quantity) if part.quantity is not None else 1.0,
            "unit": part.unit or "each",
            "specs": part.specs or {},
            "classification_confidence": float(part.classification_confidence or 0),
            "rfq_required": bool(part.rfq_required),
            "drawing_required": bool(part.drawing_required),
            "secondary_ops": part.secondary_ops or [],
            "raw_text": part.raw_text or "",
            "phase2a_bundle": phase2a_dict,
        }
        vendors = runtime_pipeline_service._load_active_vendors(db)
        fx_context = runtime_pipeline_service._build_fx_context(db=db, target_currency=target_currency)
        freight_context = runtime_pipeline_service._build_freight_context(
            db=db,
            target_currency=target_currency,
            delivery_location=delivery_location,
        )
        recommendation = runtime_pipeline_service._recommend_line(
            db=db,
            normalized=normalized,
            vendors=vendors,
            target_currency=target_currency,
            delivery_location=delivery_location,
            weight_profile=weight_profile,
            fx_context=fx_context,
            freight_context=freight_context,
        )

        part.score_cache_json = recommendation
        part.scoring_status = "COMPLETE"
        part.enrichment_status = "COMPLETE"
        if part.status in {
            BOMLineStatus.RAW,
            BOMLineStatus.NORMALIZING,
            BOMLineStatus.NORMALIZED,
            BOMLineStatus.NEEDS_REVIEW,
            BOMLineStatus.ENRICHING,
            BOMLineStatus.ENRICHED,
            BOMLineStatus.SCORING,
            BOMLineStatus.SCORED,
        }:
            part.status = BOMLineStatus.SCORED

        freshness_json = dict(part.data_freshness_json or {})
        freshness_json["phase2a"] = bundle.freshness_summary
        freshness_json["phase2a_refresh_needed"] = False
        freshness_json["phase2a_refresh_reasons"] = []
        freshness_json["phase2a_last_recomputed_at"] = self._now().isoformat()
        part.data_freshness_json = freshness_json

        execution_log.status = "completed"
        execution_log.records_written = 1
        execution_log.freshness_status = str((bundle.freshness_summary or {}).get("status") or "unknown")
        execution_log.source_metadata = {
            **(execution_log.source_metadata or {}),
            "dependencies": dependency_payload,
            "strategy_gate": recommendation.get("strategy_gate"),
        }
        execution_log.completed_at = self._now()
        execution_log.duration_ms = int(
            (execution_log.completed_at - execution_log.started_at).total_seconds() * 1000
        )

        db.flush()
        return {
            "status": "recomputed",
            "bom_line_id": bom_line_id,
            "strategy_gate": recommendation.get("strategy_gate"),
            "recommended_vendor_id": recommendation.get("recommended_vendor_id"),
            "dependencies": dependency_payload,
        }


phase2a_recompute_service = Phase2ARecomputeService()
