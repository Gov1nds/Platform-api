from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.enums import ProjectStatus
from app.models.bom import BOM, BOMPart
from app.models.enrichment import BOMLineEvidenceCoverageFact, EvidenceGapBacklogItem, EnrichmentRunLog
from app.models.project import Project


CRITICAL_CATEGORIES = {
    "missing_mapping",
    "missing_availability",
    "tariff_out_of_scope",
    "lane_missing",
    "weak_vendor_evidence",
    "stale_critical_signal",
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


class EvidenceOperationsService:
    COVERAGE_STAGE = "phase2b_batch5_coverage_snapshot"
    BACKLOG_STAGE = "phase2b_batch5_backlog_route"

    def _hash(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _taxonomy_code_for_part(self, part: BOMPart) -> str:
        return (
            part.procurement_class
            or (part.enrichment_json or {}).get("taxonomy_code")
            or (part.score_cache_json or {}).get("procurement_class")
            or "unclassified"
        )

    def _has_sku_mapping(self, part: BOMPart, phase2a: dict[str, Any]) -> bool:
        offer = phase2a.get("offer_evidence") or {}
        return bool(
            part.canonical_part_key
            or offer.get("selected_mapping_id")
            or offer.get("canonical_sku_id")
            or offer.get("canonical_offer_snapshot_id")
        )

    def _fresh_status(self, value: Any) -> bool:
        return str(value or "").strip().lower() in {"fresh", "recent"}

    def _coverage_metrics_for_part(self, part: BOMPart) -> dict[str, int]:
        phase2a = ((part.enrichment_json or {}).get("phase2a") or {})
        offer = phase2a.get("offer_evidence") or {}
        avail = phase2a.get("availability_evidence") or {}
        tariff = phase2a.get("tariff_evidence") or {}
        freight = phase2a.get("freight_evidence") or {}
        result = part.score_cache_json or {}

        return {
            "lines_total": 1,
            "lines_with_sku_mapping": int(self._has_sku_mapping(part, phase2a)),
            "lines_with_fresh_offer": int(self._fresh_status(offer.get("freshness_status"))),
            "lines_with_fresh_availability": int(self._fresh_status(avail.get("freshness_status"))),
            "lines_with_hs6": int(bool(str(tariff.get("hs_code") or "")[:6])),
            "lines_with_tariff_row": int(bool(tariff.get("tariff_schedule_id"))),
            "lines_with_lane_band": int(bool(freight.get("lane_rate_band_id"))),
            "lines_award_ready": int(result.get("strategy_gate") == "award-ready"),
            "lines_rfq_first": int(result.get("strategy_gate") == "rfq-first"),
        }

    def snapshot_coverage_facts(
        self,
        db: Session,
        *,
        tenant_id: str,
        project_id: str | None = None,
        snapshot_at: datetime | None = None,
    ) -> list[BOMLineEvidenceCoverageFact]:
        snapshot_at = snapshot_at or _now()
        snapshot_at = snapshot_at.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            db.query(BOMPart)
            .join(BOM, BOM.id == BOMPart.bom_id)
            .filter(
                BOMPart.deleted_at.is_(None),
                BOM.deleted_at.is_(None),
                BOM.organization_id == tenant_id,
            )
        )
        if project_id:
            rows = rows.filter(BOM.project_id == project_id)

        grouped: dict[tuple[str | None, str], dict[str, Any]] = defaultdict(dict)
        for part in rows.all():
            key = (part.bom.project_id if getattr(part, "bom", None) else None, self._taxonomy_code_for_part(part))
            bucket = grouped.setdefault(
                key,
                {
                    "project_id": key[0],
                    "taxonomy_code": key[1],
                    "lines_total": 0,
                    "lines_with_sku_mapping": 0,
                    "lines_with_fresh_offer": 0,
                    "lines_with_fresh_availability": 0,
                    "lines_with_hs6": 0,
                    "lines_with_tariff_row": 0,
                    "lines_with_lane_band": 0,
                    "lines_award_ready": 0,
                    "lines_rfq_first": 0,
                },
            )
            metrics = self._coverage_metrics_for_part(part)
            for metric_name, value in metrics.items():
                bucket[metric_name] += int(value)

        persisted: list[BOMLineEvidenceCoverageFact] = []
        for (bucket_project_id, taxonomy_code), payload in grouped.items():
            row = (
                db.query(BOMLineEvidenceCoverageFact)
                .filter(
                    BOMLineEvidenceCoverageFact.tenant_id == tenant_id,
                    BOMLineEvidenceCoverageFact.project_id == bucket_project_id,
                    BOMLineEvidenceCoverageFact.taxonomy_code == taxonomy_code,
                    BOMLineEvidenceCoverageFact.snapshot_date == snapshot_at,
                )
                .first()
            )
            if row is None:
                row = BOMLineEvidenceCoverageFact(
                    snapshot_date=snapshot_at,
                    tenant_id=tenant_id,
                    project_id=bucket_project_id,
                    taxonomy_code=taxonomy_code,
                )
                db.add(row)
            for field, value in payload.items():
                if field in {"project_id", "taxonomy_code"}:
                    continue
                setattr(row, field, int(value))
            row.source_metadata = {"source": "phase2b_batch5", "snapshot_kind": "point_in_time"}
            persisted.append(row)

        db.flush()
        return persisted

    def classify_evidence_gaps(
        self,
        *,
        bom_part: BOMPart,
        project: Project | None = None,
        recommendation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        phase2a = ((bom_part.enrichment_json or {}).get("phase2a") or {})
        recommendation = recommendation or (bom_part.score_cache_json or {})
        offer = phase2a.get("offer_evidence") or {}
        avail = phase2a.get("availability_evidence") or {}
        tariff = phase2a.get("tariff_evidence") or {}
        freight = phase2a.get("freight_evidence") or {}
        freshness = phase2a.get("freshness_summary") or {}
        uncertainty = phase2a.get("uncertainty_flags") or {}
        evidence_summary = recommendation.get("evidence_summary") or {}

        categories: list[dict[str, Any]] = []

        if not self._has_sku_mapping(bom_part, phase2a):
            categories.append({
                "category": "missing_mapping",
                "severity": "critical",
                "detail": {"canonical_part_key": bom_part.canonical_part_key, "reason": "no_mapping_or_canonical_sku"},
            })

        if uncertainty.get("availability_missing") or not avail.get("snapshot_id"):
            categories.append({
                "category": "missing_availability",
                "severity": "high",
                "detail": {"availability_status": avail.get("availability_status"), "reason": avail.get("uncertainty_reason")},
            })

        tariff_status = str(tariff.get("coverage_status") or tariff.get("uncertainty_reason") or "").lower()
        if uncertainty.get("tariff_uncertain") and ("out_of_scope" in tariff_status or not tariff.get("tariff_schedule_id")):
            categories.append({
                "category": "tariff_out_of_scope",
                "severity": "medium",
                "detail": {"hs_code": tariff.get("hs_code"), "coverage_status": tariff.get("coverage_status"), "reason": tariff.get("uncertainty_reason")},
            })

        freight_status = str(freight.get("coverage_status") or freight.get("uncertainty_reason") or "").lower()
        if uncertainty.get("freight_uncertain") and ("lane" in freight_status or not freight.get("lane_rate_band_id")):
            categories.append({
                "category": "lane_missing",
                "severity": "medium",
                "detail": {"lane_key": freight.get("lane_key"), "coverage_status": freight.get("coverage_status"), "reason": freight.get("uncertainty_reason")},
            })

        candidate_rankings = recommendation.get("candidate_rankings") or []
        phase2a_confidence = _as_float((phase2a.get("confidence_summary") or {}).get("score"), 0.0)
        completeness = 1.0
        if evidence_summary.get("evidence_completeness_score") is not None:
            completeness = _as_float(evidence_summary.get("evidence_completeness_score"), 0.0)
        elif uncertainty:
            completeness = max(0.0, 1.0 - (sum(1 for v in uncertainty.values() if v) / max(len(uncertainty), 1)))
        if (not candidate_rankings) or recommendation.get("recommended_vendor_id") is None or phase2a_confidence < 0.45 or completeness < 0.50:
            categories.append({
                "category": "weak_vendor_evidence",
                "severity": "high" if recommendation.get("strategy_gate") == "rfq-first" else "medium",
                "detail": {
                    "recommended_vendor_id": recommendation.get("recommended_vendor_id"),
                    "candidate_count": len(candidate_rankings),
                    "phase2a_confidence": phase2a_confidence,
                    "evidence_completeness_score": completeness,
                },
            })

        freshness_values = [
            str(freshness.get("status") or "").lower(),
            str(freshness.get("offer_status") or "").lower(),
            str(freshness.get("availability_status") or "").lower(),
            str(freshness.get("tariff_status") or "").lower(),
            str(freshness.get("freight_status") or "").lower(),
        ]
        if any(value in {"stale", "expired", "mixed"} for value in freshness_values):
            categories.append({
                "category": "stale_critical_signal",
                "severity": "high",
                "detail": {"freshness_summary": freshness},
            })

        deduped: list[dict[str, Any]] = []
        seen = set()
        for item in categories:
            if item["category"] in seen:
                continue
            seen.add(item["category"])
            deduped.append(item)
        return deduped

    def _line_request_frequency(self, db: Session, *, bom_part_id: str) -> int:
        return (
            db.query(EnrichmentRunLog)
            .filter(EnrichmentRunLog.bom_part_id == bom_part_id)
            .count()
        )

    def priority_score_for_line(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        project: Project | None = None,
        categories: Iterable[str] | None = None,
    ) -> Decimal:
        categories = set(categories or [])
        score = Decimal("0")

        if project and project.status in ACTIVE_PROJECT_STATUSES:
            score += Decimal("45")
        elif bom_part.bom and bom_part.bom.project_id:
            score += Decimal("20")

        quantity = Decimal(str(bom_part.quantity or 0)) if bom_part.quantity is not None else Decimal("0")
        estimated_line_total = _as_float((bom_part.score_cache_json or {}).get("pricing_context", {}).get("estimated_line_total"), 0.0)
        if estimated_line_total <= 0:
            top_candidate = ((bom_part.score_cache_json or {}).get("candidate_rankings") or [{}])[0]
            estimated_line_total = _as_float(top_candidate.get("estimated_line_total"), 0.0)
        value_signal = max(float(quantity), estimated_line_total)
        if value_signal >= 10000:
            score += Decimal("30")
        elif value_signal >= 1000:
            score += Decimal("18")
        elif value_signal > 0:
            score += Decimal("8")

        frequency = self._line_request_frequency(db, bom_part_id=bom_part.id)
        score += Decimal(str(min(frequency, 20)))

        for category in categories:
            if category in {"missing_mapping", "missing_availability", "stale_critical_signal"}:
                score += Decimal("12")
            elif category in CRITICAL_CATEGORIES:
                score += Decimal("7")

        return score.quantize(Decimal("0.0001"))

    def route_backlog_for_line(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        project: Project | None = None,
        recommendation: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> list[EvidenceGapBacklogItem]:
        observed_at = observed_at or _now()
        recommendation = recommendation or (bom_part.score_cache_json or {})
        gaps = self.classify_evidence_gaps(bom_part=bom_part, project=project, recommendation=recommendation)
        categories = [gap["category"] for gap in gaps]
        priority_score = self.priority_score_for_line(db, bom_part=bom_part, project=project, categories=categories)
        persisted: list[EvidenceGapBacklogItem] = []
        active_categories = set(categories)

        existing_rows = (
            db.query(EvidenceGapBacklogItem)
            .filter(EvidenceGapBacklogItem.bom_part_id == bom_part.id)
            .all()
        )
        by_category = {row.category: row for row in existing_rows}

        for gap in gaps:
            category = gap["category"]
            dedupe_key = self._hash(
                {
                    "tenant_id": bom_part.organization_id,
                    "project_id": bom_part.bom.project_id if getattr(bom_part, "bom", None) else None,
                    "bom_part_id": bom_part.id,
                    "category": category,
                }
            )
            row = by_category.get(category)
            if row is None:
                row = EvidenceGapBacklogItem(
                    tenant_id=bom_part.organization_id,
                    project_id=bom_part.bom.project_id if getattr(bom_part, "bom", None) else None,
                    bom_id=bom_part.bom_id,
                    bom_part_id=bom_part.id,
                    category=category,
                    dedupe_key=dedupe_key,
                    taxonomy_code=self._taxonomy_code_for_part(bom_part),
                    first_seen_at=observed_at,
                )
                db.add(row)
                by_category[category] = row
            else:
                row.request_count = int(row.request_count or 0) + 1
                row.resolved_at = None
            row.status = "open"
            row.severity = gap.get("severity") or "medium"
            row.priority_score = priority_score
            row.last_seen_at = observed_at
            row.detail_json = gap.get("detail") or {}
            row.source_metadata = {
                "strategy_gate": recommendation.get("strategy_gate"),
                "recommended_vendor_id": recommendation.get("recommended_vendor_id"),
                "phase2a_freshness": (recommendation.get("evidence_summary") or {}).get("phase2a_freshness"),
            }
            persisted.append(row)

        for category, row in by_category.items():
            if category not in active_categories and row.status != "resolved":
                row.status = "resolved"
                row.resolved_at = observed_at

        evidence_summary = dict(recommendation.get("evidence_summary") or {})
        evidence_summary["missing_critical_evidence_categories"] = sorted(categories)
        completeness_penalties = len(categories)
        evidence_summary["evidence_completeness_score"] = round(max(0.0, 1.0 - (completeness_penalties / 6.0)), 4)
        recommendation["evidence_summary"] = evidence_summary
        bom_part.score_cache_json = recommendation

        db.flush()
        return persisted

    def prioritized_backlog(self, db: Session, *, tenant_id: str, limit: int = 50) -> list[EvidenceGapBacklogItem]:
        return (
            db.query(EvidenceGapBacklogItem)
            .filter(
                EvidenceGapBacklogItem.tenant_id == tenant_id,
                EvidenceGapBacklogItem.status == "open",
            )
            .order_by(EvidenceGapBacklogItem.priority_score.desc(), EvidenceGapBacklogItem.last_seen_at.desc())
            .limit(limit)
            .all()
        )


evidence_operations_service = EvidenceOperationsService()