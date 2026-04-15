"""
Phase 1 runtime procurement pipeline.

This service keeps orchestration ownership in platform-api while treating the
analyzer as a stateless normalization service. The pipeline performs:

1. Load persisted BOM + raw lines
2. Normalize each line via analyzer
3. Enrich locally using:
   - seeded vendors
   - live FX (with seeded/cache fallback already handled by FX service)
   - freight baseline
4. Score vendors deterministically
5. Generate a project recommendation
6. Persist analysis, rankings, evidence, audit, and report snapshots

Phase 1 constraints:
- no ERP
- no tariff engine
- no distributor marketplace calls
- no advanced optimization
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.enums import BOMLineStatus, ProjectStatus
from app.models.bom import AnalysisResult, BOM, BOMPart
from app.models.events import EventAuditLog, ReportSnapshot
from app.models.market import FXRate, FreightRate
from app.models.outcomes import AnomalyFlag
from app.models.project import Project
from app.models.vendor import Vendor, VendorMatch, VendorMatchRun
from app.schemas.recommendation import (
    LineRecommendationEntry,
    ProjectRecommendationResponse,
    RecommendationEvidence,
    RecommendationFreshness,
    RecommendationPricingContext,
    RecommendationSummary,
    VendorRankingEntry,
)
from app.services.analyzer_service import call_normalize
from app.services.confidence_calibration_service import confidence_calibration_service
from app.services.enrichment.evidence_operations_service import evidence_operations_service
from app.services.enrichment.phase2a_evidence_service import phase2a_evidence_service
from app.services.market_data.fx_service import fx_service
from app.services.outcome_data_service import outcome_data_service
from app.services.outcome_informed_scoring_service import outcome_informed_scoring_service
from app.services.recommendation_stability_service import recommendation_stability_service
from app.services.scoring.vendor_scorer import rank_vendors

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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _upper_or(default: str, value: str | None) -> str:
    return (value or default).upper()


def _contains_ci(values: list[str], probe: str | None) -> bool:
    if not probe:
        return False
    p = probe.strip().lower()
    return any(p in str(v).strip().lower() or str(v).strip().lower() in p for v in values if v)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class RuntimePipelineService:
    ANALYSIS_VERSION = "phase1-runtime-v1"

    def run_project_pipeline(
        self,
        db: Session,
        *,
        project: Project,
        actor_id: str | None = None,
        trace_id: str | None = None,
    ) -> ProjectRecommendationResponse:
        bom = (
            db.query(BOM)
            .filter(BOM.id == project.bom_id, BOM.deleted_at.is_(None))
            .first()
        )
        if not bom:
            raise ValueError("Project BOM not found")

        parts = (
            db.query(BOMPart)
            .filter(BOMPart.bom_id == bom.id, BOMPart.deleted_at.is_(None))
            .order_by(BOMPart.row_number.asc().nullslast(), BOMPart.created_at.asc())
            .all()
        )
        if not parts:
            raise ValueError("No BOM parts found for this project")

        target_currency = _upper_or(
            "USD",
            bom.target_currency
            or (project.project_metadata or {}).get("target_currency")
            or "USD",
        )
        delivery_location = (
            bom.delivery_location
            or (project.project_metadata or {}).get("delivery_location")
            or ""
        )
        weight_profile = project.weight_profile or "balanced"

        if project.status in {ProjectStatus.DRAFT, ProjectStatus.INTAKE_COMPLETE}:
            project.status = ProjectStatus.ANALYSIS_IN_PROGRESS
            db.flush()

        normalized_lines: list[dict[str, Any]] = []
        analyzer_evidence: list[dict[str, Any]] = []
        prior_recommendation = self.get_latest_recommendation(db, project_id=project.id)
        prior_line_map = {
            row.bom_part_id: row.model_dump() if hasattr(row, "model_dump") else row
            for row in (prior_recommendation.line_recommendations if prior_recommendation else [])
        }

        try:
            for part in parts:
                normalized = self._normalize_part(part=part, trace_id=trace_id)
                phase2a_bundle = self._ensure_phase2a_bundle(
                    db=db,
                    bom=bom,
                    project=project,
                    part=part,
                    target_currency=target_currency,
                    trace_id=trace_id,
                )
                if phase2a_bundle:
                    normalized["phase2a_bundle"] = phase2a_bundle
                normalized_lines.append(normalized)
                analyzer_evidence.append(
                    {
                        "bom_part_id": part.id,
                        "canonical_part_key": normalized.get("canonical_part_key"),
                        "classification_confidence": normalized.get("classification_confidence"),
                        "procurement_class": normalized.get("procurement_class"),
                        "phase2a_used": bool(phase2a_bundle),
                    }
                )

            active_vendors = self._load_active_vendors(db)
            if not active_vendors:
                raise ValueError("No active seeded vendors available for Phase 1 recommendation")

            line_outputs: list[dict[str, Any]] = []
            aggregate: dict[str, dict[str, Any]] = {}

            fx_context = self._build_fx_context(db=db, target_currency=target_currency)
            freight_context = self._build_freight_context(
                db=db,
                target_currency=target_currency,
                delivery_location=delivery_location,
            )

            for normalized in normalized_lines:
                line_result = self._recommend_line(
                    db=db,
                    normalized=normalized,
                    vendors=active_vendors,
                    target_currency=target_currency,
                    delivery_location=delivery_location,
                    weight_profile=weight_profile,
                    fx_context=fx_context,
                    freight_context=freight_context,
                    previous_line=prior_line_map.get(normalized["bom_part_id"]),
                )
                line_outputs.append(line_result)
                self._accumulate_vendor_rollup(aggregate, line_result)

            part_index = {part.id: part for part in parts}
            for line_result in line_outputs:
                part = part_index.get(line_result.get("bom_part_id"))
                if not part:
                    continue
                evidence_operations_service.route_backlog_for_line(
                    db,
                    bom_part=part,
                    project=project,
                    recommendation=line_result,
                    observed_at=_now(),
                )
                part.score_cache_json = line_result
            if bom.organization_id:
                evidence_operations_service.snapshot_coverage_facts(
                    db,
                    tenant_id=bom.organization_id,
                    project_id=project.id,
                    snapshot_at=_now(),
                )

            vendor_rankings = self._build_project_vendor_rankings(aggregate)
            summary = self._build_summary(
                target_currency=target_currency,
                line_outputs=line_outputs,
                vendor_rankings=vendor_rankings,
            )
            freshness = self._build_freshness(
                fx_context=fx_context,
                freight_context=freight_context,
                line_outputs=line_outputs,
            )
            evidence = self._build_evidence(
                analyzer_evidence=analyzer_evidence,
                line_outputs=line_outputs,
                vendor_rankings=vendor_rankings,
                fx_context=fx_context,
                freight_context=freight_context,
            )

            recommendation = ProjectRecommendationResponse(
                project_id=project.id,
                bom_id=bom.id,
                generated_at=_now(),
                status="success",
                summary=summary,
                freshness=freshness,
                vendor_rankings=vendor_rankings,
                line_recommendations=[
                    LineRecommendationEntry(**entry) for entry in line_outputs
                ],
                evidence=evidence,
            )

            self._persist_outputs(
                db=db,
                project=project,
                bom=bom,
                recommendation=recommendation,
                actor_id=actor_id,
                trace_id=trace_id,
                weight_profile=weight_profile,
            )

            db.commit()
            return recommendation

        except Exception as exc:
            db.rollback()
            logger.exception("Phase 1 runtime pipeline failed for project %s", project.id)
            self._audit_failure(
                db=db,
                project=project,
                actor_id=actor_id,
                trace_id=trace_id,
                error=str(exc),
            )
            db.commit()
            raise

    def get_latest_recommendation(
        self,
        db: Session,
        *,
        project_id: str,
    ) -> ProjectRecommendationResponse | None:
        snapshot = (
            db.query(ReportSnapshot)
            .filter(
                ReportSnapshot.scope_type == "project",
                ReportSnapshot.scope_id == project_id,
                ReportSnapshot.report_type == "procurement_recommendation",
            )
            .order_by(ReportSnapshot.created_at.desc())
            .first()
        )
        if snapshot and snapshot.data_json:
            return ProjectRecommendationResponse.model_validate(snapshot.data_json)

        project = db.query(Project).filter(Project.id == project_id).first()
        if project and project.analyzer_report:
            try:
                return ProjectRecommendationResponse.model_validate(project.analyzer_report)
            except Exception:
                return None
        return None

    def _normalize_part(
        self,
        *,
        part: BOMPart,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bom_line_id": part.id,
            "raw_text": part.raw_text or part.description or "",
            "description": part.description or part.raw_text or "",
            "quantity": _as_float(part.quantity, 1.0),
            "unit": part.unit or "each",
            "specs": part.specs or {},
        }
        result = _run_async(call_normalize(payload, trace_id=trace_id)) or {}

        normalized_text = (
            result.get("normalized_text")
            or result.get("normalized_description")
            or result.get("normalized_name")
            or part.normalized_text
            or part.description
            or part.raw_text
            or ""
        )
        procurement_class = (
            result.get("procurement_class")
            or result.get("category")
            or part.procurement_class
            or "unknown"
        )
        classification_confidence = _as_float(
            result.get("classification_confidence", part.classification_confidence),
            0.0,
        )

        part.normalized_text = normalized_text
        part.procurement_class = procurement_class
        part.material = result.get("material") or part.material
        part.material_form = result.get("material_form") or part.material_form
        part.geometry = result.get("geometry") or part.geometry
        part.tolerance = result.get("tolerance") or part.tolerance
        part.secondary_ops = result.get("secondary_ops") or part.secondary_ops or []
        part.specs = {**(part.specs or {}), **(result.get("specs") or {})}
        part.classification_confidence = _as_decimal(classification_confidence, "0")
        part.classification_reason = result.get("classification_reason") or part.classification_reason
        part.has_mpn = bool(result.get("has_mpn", part.has_mpn))
        part.is_custom = bool(result.get("is_custom", part.is_custom))
        part.is_raw = bool(result.get("is_raw", part.is_raw))
        part.rfq_required = bool(result.get("requires_rfq", result.get("rfq_required", part.rfq_required)))
        part.drawing_required = bool(result.get("drawing_required", part.drawing_required))
        part.canonical_part_key = (
            result.get("canonical_part_key")
            or result.get("canonical_key")
            or part.canonical_part_key
        )
        part.normalization_status = "COMPLETE"
        part.normalization_trace_json = result
        part.status = BOMLineStatus.NORMALIZED

        return {
            "bom_part_id": part.id,
            "row_number": part.row_number,
            "canonical_part_key": part.canonical_part_key,
            "normalized_text": part.normalized_text,
            "procurement_class": part.procurement_class,
            "material": part.material,
            "material_form": part.material_form,
            "quantity": _as_float(part.quantity, 0.0),
            "unit": part.unit or "each",
            "specs": part.specs or {},
            "classification_confidence": classification_confidence,
            "rfq_required": bool(part.rfq_required),
            "drawing_required": bool(part.drawing_required),
            "secondary_ops": part.secondary_ops or [],
            "raw_text": part.raw_text or "",
        }

    def _load_active_vendors(self, db: Session) -> list[Vendor]:
        return (
            db.query(Vendor)
            .options(selectinload(Vendor.capabilities))
            .filter(Vendor.deleted_at.is_(None), Vendor.is_active.is_(True))
            .all()
        )

    def _ensure_phase2a_bundle(
        self,
        *,
        db: Session,
        bom: BOM,
        project: Project,
        part: BOMPart,
        target_currency: str,
        trace_id: str | None,
    ) -> dict[str, Any] | None:
        existing = (part.enrichment_json or {}).get("phase2a")
        if isinstance(existing, dict) and existing:
            return existing
        try:
            bundle = phase2a_evidence_service.assemble_for_bom_part(
                db,
                bom_part=part,
                bom=bom,
                project=project,
                target_currency=target_currency,
                trace_id=trace_id,
            )
            return {
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
        except Exception as exc:
            logger.warning("Phase 2A bundle assembly failed for part %s: %s", part.id, exc)
            return existing if isinstance(existing, dict) else None

    def _recommend_line(
        self,
        *,
        db: Session,
        normalized: dict[str, Any],
        vendors: list[Vendor],
        target_currency: str,
        delivery_location: str,
        weight_profile: str,
        fx_context: dict[str, Any],
        freight_context: dict[str, Any],
        previous_line: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        quantity = max(_as_float(normalized.get("quantity"), 0.0), 1.0)
        material = normalized.get("material")
        procurement_class = normalized.get("procurement_class") or "unknown"
        secondary_ops = normalized.get("secondary_ops") or []
        required_processes = [procurement_class] + [str(op) for op in secondary_ops if op]
        phase2a_bundle = normalized.get("phase2a_bundle") if isinstance(normalized.get("phase2a_bundle"), dict) else {}

        candidate_vendor_dicts: list[dict[str, Any]] = []
        for vendor in vendors:
            vendor_dict = self._vendor_to_candidate_dict(
                db=db,
                vendor=vendor,
                normalized=normalized,
                target_currency=target_currency,
                delivery_location=delivery_location,
                quantity=quantity,
                fx_context=fx_context,
                freight_context=freight_context,
            )
            if vendor_dict["eligibility"]["matched"]:
                candidate_vendor_dicts.append(vendor_dict)

        pricing_context = RecommendationPricingContext(
            source_currency=target_currency,
            target_currency=target_currency,
            fx_rate=1.0,
            fx_source=fx_context["fx_source"],
            fx_timestamp=fx_context["fx_timestamp"],
            freight_mode=freight_context["freight_mode"],
            freight_currency=freight_context["freight_currency"],
            freight_rate_per_kg=freight_context["freight_rate_per_kg"],
            freight_min_charge=freight_context["freight_min_charge"],
        )

        if not candidate_vendor_dicts:
            gate = self._phase2a_strategy_gate(phase2a_bundle=phase2a_bundle, has_recommendation=False)
            confidence_score = self._evidence_weighted_confidence_score(
                normalized_confidence=_as_float(normalized.get("classification_confidence"), 0.0),
                freshness_status=fx_context["overall_status"],
                has_full_match=False,
                phase2a_bundle=phase2a_bundle,
            )
            return {
                "bom_part_id": normalized["bom_part_id"],
                "row_number": normalized.get("row_number"),
                "canonical_part_key": normalized.get("canonical_part_key"),
                "normalized_text": normalized.get("normalized_text"),
                "procurement_class": procurement_class,
                "quantity": quantity,
                "unit": normalized.get("unit"),
                "confidence": round(confidence_score, 4),
                "recommended_vendor_id": None,
                "recommended_vendor_name": None,
                "rationale": "No seeded vendor matched the normalized process/material requirements for this BOM line.",
                "freshness_status": fx_context["overall_status"],
                "pricing_context": pricing_context.model_dump(),
                "candidate_rankings": [],
                "strategy_gate": gate["strategy_gate"],
                "strategy_reasons": gate["strategy_reasons"],
                "evidence_summary": {
                    "phase2a_used": bool(phase2a_bundle),
                    "phase2a_confidence": (phase2a_bundle.get("confidence_summary") or {}).get("score"),
                    "phase2a_freshness": (phase2a_bundle.get("freshness_summary") or {}).get("status"),
                    "phase2a_uncertainty_flags": phase2a_bundle.get("uncertainty_flags") or {},
                    "missing_critical_evidence_categories": [],
                    "evidence_completeness_score": None,
                },
            }

        market_median = statistics.median(
            [v["estimated_unit_price_target"] for v in candidate_vendor_dicts]
        ) if candidate_vendor_dicts else None

        requirements = {
            "processes": required_processes,
            "materials": [material] if material else [],
            "target_lead_time_days": 30,
            "delivery_region": delivery_location,
            "required_certifications": [],
            "total_quantity": quantity,
        }
        outcome_intelligence_by_vendor: dict[str, Any] = {}
        for candidate in candidate_vendor_dicts:
            anomaly_summary = self._build_anomaly_summary(
                db=db,
                bom_line_id=normalized["bom_part_id"],
                vendor_id=candidate["id"],
            )
            adjustment = outcome_informed_scoring_service.build_adjustment(
                db,
                vendor_id=candidate["id"],
                bom_line_id=normalized["bom_part_id"],
                anomaly_summary=anomaly_summary,
                adjusted_lead_time_days=candidate.get("avg_lead_time_days"),
            )
            outcome_intelligence_by_vendor[candidate["id"]] = {
                **adjustment.to_dict(),
                "anomaly_summary": anomaly_summary,
            }

        market_ctx = {
            "fx_rate": fx_context["fx_rate"],
            "freight_per_kg": freight_context["freight_rate_per_kg"],
            "data_age_days": fx_context["fx_age_days"],
            "market_median_price": market_median,
            "phase2a": phase2a_bundle,
            "outcome_intelligence_by_vendor": outcome_intelligence_by_vendor,
        }

        ranked = rank_vendors(
            candidate_vendor_dicts,
            requirements=requirements,
            market_ctx=market_ctx,
            weights=None,
        )

        candidate_rankings: list[VendorRankingEntry] = []
        for entry in ranked:
            candidate = next(v for v in candidate_vendor_dicts if v["id"] == entry["vendor_id"])
            freshness_status = entry.get("evidence_freshness") or entry.get("market_freshness", "fresh")
            raw_confidence_score = self._evidence_weighted_confidence_score(
                normalized_confidence=_as_float(normalized.get("classification_confidence"), 0.0),
                freshness_status=freshness_status,
                has_full_match=candidate["eligibility"]["matched"],
                phase2a_bundle=phase2a_bundle,
            )
            calibration = confidence_calibration_service.map_confidence(
                db,
                raw_confidence=raw_confidence_score,
            )
            calibrated_confidence_score = _as_float(calibration.calibrated_confidence, raw_confidence_score)
            confidence = self._confidence_label(confidence_score=calibrated_confidence_score)
            outcome_adjustment = outcome_intelligence_by_vendor.get(entry["vendor_id"], {})
            anomaly_summary = outcome_adjustment.get("anomaly_summary") or self._build_anomaly_summary(
                db=db,
                bom_line_id=normalized["bom_part_id"],
                vendor_id=entry["vendor_id"],
            )
            calibrated_confidence_score = max(0.0, min(1.0, calibrated_confidence_score + _as_float(outcome_adjustment.get("confidence_adjustment"), 0.0)))
            confidence = self._confidence_label(confidence_score=calibrated_confidence_score)
            explanation = entry["explanation"]
            extra_fragments = outcome_adjustment.get("explanation_fragments") or []
            if extra_fragments:
                explanation = f"{explanation}; {'; '.join(extra_fragments)}"
            candidate_rankings.append(
                VendorRankingEntry(
                    vendor_id=entry["vendor_id"],
                    vendor_name=entry["vendor_name"],
                    rank=entry["rank"],
                    score=round(_as_float(entry["total_score"]), 4),
                    confidence=confidence,
                    confidence_score=round(calibrated_confidence_score, 4),
                    raw_confidence_score=round(raw_confidence_score, 4),
                    calibrated_confidence_score=round(calibrated_confidence_score, 4),
                    rationale=explanation,
                    freshness_status=freshness_status,
                    source_currency=candidate["source_currency"],
                    target_currency=target_currency,
                    fx_rate=round(candidate["fx_rate"], 6),
                    estimated_unit_price=round(candidate["estimated_unit_price_target"], 6),
                    estimated_line_total=round(candidate["estimated_line_total"], 6),
                    estimated_project_total=round(candidate["estimated_line_total"], 6),
                    estimated_freight_total=round(candidate["estimated_freight_total"], 6),
                    average_lead_time_days=_as_float(candidate.get("avg_lead_time_days"), 0.0) or None,
                    score_breakdown={k: round(_as_float(v), 4) for k, v in (entry.get("breakdown") or {}).items()},
                    evidence={
                        "processes": required_processes,
                        "material": material,
                        "vendor_capabilities": candidate["capabilities"],
                        "eligibility": candidate["eligibility"],
                        "pricing": candidate["pricing"],
                        "phase2a": phase2a_bundle,
                        "anomaly_summary": anomaly_summary,
                        "outcome_adjustment": outcome_adjustment,
                        "confidence_calibration": {
                            "used_calibration": calibration.used_calibration,
                            "fallback_reason": calibration.fallback_reason,
                            "band_sample_size": calibration.band_sample_size,
                            "score_range_min": str(calibration.score_range_min) if calibration.score_range_min is not None else None,
                            "score_range_max": str(calibration.score_range_max) if calibration.score_range_max is not None else None,
                        },
                    },
                )
            )

        stability = recommendation_stability_service.apply(
            candidate_rankings=candidate_rankings,
            previous_line=previous_line,
        )
        candidate_rankings = stability.candidate_rankings
        top = candidate_rankings[0]
        gate = self._phase2a_strategy_gate(phase2a_bundle=phase2a_bundle, has_recommendation=True)
        top_outcome_adjustment = (top.evidence or {}).get("outcome_adjustment") or {}
        if top_outcome_adjustment.get("strategy_gate_bias") == "rfq-first" and gate.get("strategy_gate") != "rfq-first":
            gate["strategy_gate"] = "rfq-first"
            gate.setdefault("strategy_reasons", []).append("Outcome anomalies require RFQ-first verification.")
        pricing_context = RecommendationPricingContext(
            source_currency=top.source_currency,
            target_currency=top.target_currency,
            fx_rate=top.fx_rate,
            fx_source=fx_context["fx_source"],
            fx_timestamp=fx_context["fx_timestamp"],
            freight_mode=freight_context["freight_mode"],
            freight_currency=freight_context["freight_currency"],
            freight_rate_per_kg=freight_context["freight_rate_per_kg"],
            freight_min_charge=freight_context["freight_min_charge"],
        )

        return {
            "bom_part_id": normalized["bom_part_id"],
            "row_number": normalized.get("row_number"),
            "canonical_part_key": normalized.get("canonical_part_key"),
            "normalized_text": normalized.get("normalized_text"),
            "procurement_class": procurement_class,
            "quantity": quantity,
            "unit": normalized.get("unit"),
            "confidence": round(top.confidence_score or _as_float(normalized.get("classification_confidence"), 0.0), 4),
            "recommended_vendor_id": top.vendor_id,
            "recommended_vendor_name": top.vendor_name,
            "rationale": f"{top.vendor_name} ranked first for this line based on capability fit, converted price, freight baseline, evidence quality, data freshness, and deterministic outcome-informed adjustments.",
            "freshness_status": top.freshness_status,
            "pricing_context": pricing_context.model_dump(),
            "candidate_rankings": [c.model_dump() for c in candidate_rankings],
            "strategy_gate": gate["strategy_gate"],
            "strategy_reasons": gate["strategy_reasons"],
            "evidence_summary": {
                "phase2a_used": bool(phase2a_bundle),
                "phase2a_confidence": (phase2a_bundle.get("confidence_summary") or {}).get("score"),
                "phase2a_freshness": (phase2a_bundle.get("freshness_summary") or {}).get("status"),
                "phase2a_uncertainty_flags": phase2a_bundle.get("uncertainty_flags") or {},
                "missing_critical_evidence_categories": [],
                "evidence_completeness_score": None,
                "calibration_used": bool((top.evidence or {}).get("confidence_calibration", {}).get("used_calibration")),
                "calibration_fallback_reason": (top.evidence or {}).get("confidence_calibration", {}).get("fallback_reason"),
                "anomaly_summary": (top.evidence or {}).get("anomaly_summary") or {},
                "outcome_adjustment": (top.evidence or {}).get("outcome_adjustment") or {},
            },
            "rank_changed": stability.rank_changed,
            "prior_rank": stability.prior_rank,
            "score_delta": round(stability.score_delta, 4) if stability.score_delta is not None else None,
            "material_change_flag": stability.material_change_flag,
            "stability_reason": stability.stability_reason,
        }

    def _build_anomaly_summary(self, *, db: Session, bom_line_id: str, vendor_id: str) -> dict[str, Any]:
        rows = db.query(AnomalyFlag).order_by(AnomalyFlag.detected_at.desc()).limit(400).all()
        matched = []
        for row in rows:
            context = row.source_context_json or {}
            if context.get("bom_line_id") == bom_line_id and context.get("vendor_id") == vendor_id:
                matched.append(row)

        def _metric_bucket(metric_names: tuple[str, ...], anomaly_types: tuple[str, ...]) -> dict[str, Any]:
            bucket = [
                row for row in matched
                if (row.metric_name in metric_names) or (row.anomaly_type in anomaly_types)
            ]
            return {
                "count": len(bucket),
                "has_high_severity": any((row.severity or "").lower() == "high" for row in bucket),
                "latest_detected_at": bucket[0].detected_at.isoformat() if bucket else None,
                "latest_anomaly_type": bucket[0].anomaly_type if bucket else None,
            }

        price_bucket = _metric_bucket(("quoted_price", "accepted_price", "unit_price"), ("price_outlier",))
        lead_bucket = _metric_bucket(("quoted_lead_time", "actual_lead_time", "lead_time_diff_days"), ("lead_time_outlier", "lead_time_diff_outlier"))
        availability_bucket = _metric_bucket(("available_quantity", "stock_quantity", "availability_status"), ("availability_jump", "availability_contradiction", "invalid_stock_value"))
        return {
            "count": len(matched),
            "has_high_severity": any((row.severity or "").lower() == "high" for row in matched),
            "latest_detected_at": matched[0].detected_at.isoformat() if matched else None,
            "latest_anomaly_type": matched[0].anomaly_type if matched else None,
            "price": price_bucket,
            "lead_time": lead_bucket,
            "availability": availability_bucket,
        }

    def _vendor_to_candidate_dict(
        self,
        *,
        db: Session,
        vendor: Vendor,
        normalized: dict[str, Any],
        target_currency: str,
        delivery_location: str,
        quantity: float,
        fx_context: dict[str, Any],
        freight_context: dict[str, Any],
    ) -> dict[str, Any]:
        process_probe = str(normalized.get("procurement_class") or "").lower()
        material_probe = str(normalized.get("material") or "").lower()

        capabilities = []
        for cap in vendor.capabilities or []:
            capabilities.append(
                {
                    "process": cap.process,
                    "material_family": cap.material_family,
                    "proficiency": _as_float(cap.proficiency, 0.8),
                }
            )

        process_hits = [
            c for c in capabilities
            if _contains_ci([c.get("process", "")], process_probe)
        ]
        material_hits = [
            c for c in capabilities
            if material_probe and _contains_ci([c.get("material_family", "")], material_probe)
        ]

        region_match = _contains_ci(vendor.regions_served or [], delivery_location) or not (vendor.regions_served or [])
        matched = bool(process_hits or material_hits) and region_match

        source_currency = _upper_or("USD", vendor.default_currency or "USD")
        fx_rate = 1.0
        if source_currency != target_currency:
            try:
                fx_rate = _as_float(
                    fx_service.get_rate(
                        db,
                        base_currency=source_currency,
                        quote_currency=target_currency,
                    ),
                    fx_context["fx_rate"],
                )
            except Exception:
                fx_rate = fx_context["fx_rate"]

        estimated_unit_price_source = self._estimate_unit_price_source(vendor, normalized)
        estimated_unit_price_target = estimated_unit_price_source * fx_rate

        estimated_weight_kg = self._estimate_line_weight_kg(normalized, quantity)
        freight_rate_per_kg = freight_context["freight_rate_per_kg"] or 0.0
        estimated_freight_total = max(
            estimated_weight_kg * freight_rate_per_kg,
            freight_context["freight_min_charge"] or 0.0,
        ) if freight_rate_per_kg or freight_context["freight_min_charge"] else 0.0

        estimated_line_total = (estimated_unit_price_target * quantity) + estimated_freight_total

        adjusted_lead_time = None
        try:
            adjusted_lead_time = outcome_data_service.get_adjusted_lead_time(
                db,
                vendor_id=vendor.id,
                bom_line_id=normalized["bom_part_id"],
            )
        except Exception:
            adjusted_lead_time = None
        quoted_lead_time_days = _as_float(vendor.avg_lead_time_days, 30.0)
        effective_lead_time_days = _as_float(adjusted_lead_time, quoted_lead_time_days)

        return {
            "id": vendor.id,
            "name": vendor.name,
            "typical_unit_price": estimated_unit_price_target,
            "avg_lead_time_days": effective_lead_time_days,
            "reliability_score": _as_float(vendor.reliability_score, 0.8),
            "regions_served": vendor.regions_served or [],
            "certifications": vendor.certifications or [],
            "capacity_profile": vendor.capacity_profile or {},
            "capabilities": capabilities,
            "source_currency": source_currency,
            "target_currency": target_currency,
            "fx_rate": fx_rate,
            "estimated_unit_price_source": estimated_unit_price_source,
            "estimated_unit_price_target": estimated_unit_price_target,
            "estimated_line_total": estimated_line_total,
            "estimated_freight_total": estimated_freight_total,
            "pricing": {
                "source_currency": source_currency,
                "target_currency": target_currency,
                "estimated_unit_price_source": estimated_unit_price_source,
                "estimated_unit_price_target": estimated_unit_price_target,
                "estimated_line_total": estimated_line_total,
                "estimated_freight_total": estimated_freight_total,
            },
            "eligibility": {
                "matched": matched,
                "process_hits": process_hits,
                "material_hits": material_hits,
                "region_match": region_match,
            },
        }

    def _accumulate_vendor_rollup(
        self,
        aggregate: dict[str, dict[str, Any]],
        line_result: dict[str, Any],
    ) -> None:
        for candidate in line_result.get("candidate_rankings", []):
            vendor_id = candidate["vendor_id"]
            bucket = aggregate.setdefault(
                vendor_id,
                {
                    "vendor_id": vendor_id,
                    "vendor_name": candidate["vendor_name"],
                    "scores": [],
                    "confidence_scores": [],
                    "project_total": 0.0,
                    "freight_total": 0.0,
                    "lead_times": [],
                    "evidence": [],
                    "freshness": [],
                    "source_currency": candidate["source_currency"],
                    "target_currency": candidate["target_currency"],
                    "fx_rate": candidate["fx_rate"],
                },
            )
            bucket["scores"].append(_as_float(candidate["score"]))
            if candidate.get("confidence_score") is not None:
                bucket["confidence_scores"].append(_as_float(candidate["confidence_score"]))
            bucket["project_total"] += _as_float(candidate.get("estimated_line_total"))
            bucket["freight_total"] += _as_float(candidate.get("estimated_freight_total"))
            if candidate.get("average_lead_time_days") is not None:
                bucket["lead_times"].append(_as_float(candidate["average_lead_time_days"]))
            bucket["evidence"].append(
                {
                    "bom_part_id": line_result["bom_part_id"],
                    "rank": candidate["rank"],
                    "score_breakdown": candidate.get("score_breakdown") or {},
                    "strategy_gate": line_result.get("strategy_gate"),
                    "strategy_reasons": line_result.get("strategy_reasons") or [],
                }
            )
            bucket["freshness"].append(candidate.get("freshness_status", "fresh"))

    def _build_project_vendor_rankings(
        self,
        aggregate: dict[str, dict[str, Any]],
    ) -> list[VendorRankingEntry]:
        rows = []
        for vendor_id, payload in aggregate.items():
            avg_score = statistics.mean(payload["scores"]) if payload["scores"] else 0.0
            avg_confidence_score = statistics.mean(payload["confidence_scores"]) if payload["confidence_scores"] else avg_score
            avg_lead = statistics.mean(payload["lead_times"]) if payload["lead_times"] else None
            freshness = self._collapse_status(payload["freshness"])
            confidence = self._confidence_label(confidence_score=avg_confidence_score)
            rows.append(
                {
                    "vendor_id": vendor_id,
                    "vendor_name": payload["vendor_name"],
                    "score": round(avg_score, 4),
                    "confidence": confidence,
                    "confidence_score": round(avg_confidence_score, 4),
                    "freshness_status": freshness,
                    "source_currency": payload["source_currency"],
                    "target_currency": payload["target_currency"],
                    "fx_rate": payload["fx_rate"],
                    "estimated_project_total": round(payload["project_total"], 6),
                    "estimated_freight_total": round(payload["freight_total"], 6),
                    "average_lead_time_days": round(avg_lead, 2) if avg_lead is not None else None,
                    "evidence": {
                        "line_count": len(payload["evidence"]),
                        "line_evidence": payload["evidence"],
                    },
                }
            )

        rows.sort(key=lambda row: (-row["score"], row["estimated_project_total"]))
        result: list[VendorRankingEntry] = []
        for idx, row in enumerate(rows, start=1):
            result.append(
                VendorRankingEntry(
                    vendor_id=row["vendor_id"],
                    vendor_name=row["vendor_name"],
                    rank=idx,
                    score=row["score"],
                    confidence=row["confidence"],
                    confidence_score=row["confidence_score"],
                    rationale=f"{row['vendor_name']} ranked #{idx} by average line score, evidence-aware confidence, and estimated converted project total.",
                    freshness_status=row["freshness_status"],
                    source_currency=row["source_currency"],
                    target_currency=row["target_currency"],
                    fx_rate=_as_float(row["fx_rate"], 1.0),
                    estimated_project_total=row["estimated_project_total"],
                    estimated_freight_total=row["estimated_freight_total"],
                    average_lead_time_days=row["average_lead_time_days"],
                    score_breakdown={},
                    evidence=row["evidence"],
                )
            )
        return result

    def _build_summary(
        self,
        *,
        target_currency: str,
        line_outputs: list[dict[str, Any]],
        vendor_rankings: list[VendorRankingEntry],
    ) -> RecommendationSummary:
        top_vendor = vendor_rankings[0] if vendor_rankings else None
        totals = [
            _as_float(v.estimated_project_total)
            for v in vendor_rankings
            if v.estimated_project_total is not None
        ]
        lead_times = [
            _as_float(v.average_lead_time_days)
            for v in vendor_rankings
            if v.average_lead_time_days is not None
        ]
        confidence_scores = [
            _as_float(v.confidence_score)
            for v in vendor_rankings
            if v.confidence_score is not None
        ]
        strategy_gates = [line.get("strategy_gate", "verify-first") for line in line_outputs]
        strategy_gate = "award-ready"
        if any(gate == "rfq-first" for gate in strategy_gates):
            strategy_gate = "rfq-first"
        elif any(gate == "verify-first" for gate in strategy_gates):
            strategy_gate = "verify-first"

        avg_confidence = statistics.mean(confidence_scores) if confidence_scores else 0.0
        overall_confidence = self._confidence_label(confidence_score=avg_confidence)

        rationale = (
            f"{top_vendor.vendor_name} is the top Phase 2A recommendation based on seeded vendor fit, converted pricing, freight baseline, evidence-weighted confidence, and freshness-aware scoring."
            if top_vendor
            else "No vendor recommendation could be produced for the current BOM."
        )

        return RecommendationSummary(
            recommended_vendor_id=top_vendor.vendor_id if top_vendor else None,
            recommended_vendor_name=top_vendor.vendor_name if top_vendor else None,
            total_lines=len(line_outputs),
            ranked_vendor_count=len(vendor_rankings),
            matched_vendor_count=sum(1 for line in line_outputs if line.get("recommended_vendor_id")),
            target_currency=target_currency,
            estimated_project_total=round(_as_float(top_vendor.estimated_project_total), 6) if top_vendor and top_vendor.estimated_project_total is not None else None,
            cost_range_low=round(min(totals), 6) if totals else None,
            cost_range_high=round(max(totals), 6) if totals else None,
            estimated_lead_time_days=round(statistics.mean(lead_times), 2) if lead_times else None,
            confidence=overall_confidence,
            rationale=rationale,
            strategy_gate=strategy_gate,
        )

    def _build_freshness(
        self,
        *,
        fx_context: dict[str, Any],
        freight_context: dict[str, Any],
        line_outputs: list[dict[str, Any]],
    ) -> RecommendationFreshness:
        line_statuses = [line.get("freshness_status", "fresh") for line in line_outputs]
        overall = self._collapse_status(
            [fx_context["overall_status"], freight_context["overall_status"], *line_statuses]
        )
        notes = []
        if fx_context["overall_status"] != "fresh":
            notes.append("FX used fallback or stale persisted data.")
        if freight_context["overall_status"] != "fresh":
            notes.append("Freight used seeded baseline data.")
        phase2a_gates = sorted({line.get("strategy_gate") for line in line_outputs if line.get("strategy_gate")})
        if phase2a_gates:
            notes.append(f"Phase 2A strategy gates present: {', '.join(phase2a_gates)}.")
        return RecommendationFreshness(
            overall_status=overall,
            fx_status=fx_context["overall_status"],
            freight_status=freight_context["overall_status"],
            analyzer_status="fresh",
            notes=notes,
        )

    def _build_evidence(
        self,
        *,
        analyzer_evidence: list[dict[str, Any]],
        line_outputs: list[dict[str, Any]],
        vendor_rankings: list[VendorRankingEntry],
        fx_context: dict[str, Any],
        freight_context: dict[str, Any],
    ) -> RecommendationEvidence:
        strategy_gates: dict[str, int] = {}
        for line in line_outputs:
            gate = line.get("strategy_gate") or "verify-first"
            strategy_gates[gate] = strategy_gates.get(gate, 0) + 1
        return RecommendationEvidence(
            analyzer_runs=analyzer_evidence,
            scoring_inputs={
                "line_count": len(line_outputs),
                "matched_line_count": sum(1 for line in line_outputs if line.get("recommended_vendor_id")),
                "phase2a_line_count": sum(1 for line in line_outputs if (line.get("evidence_summary") or {}).get("phase2a_used")),
            },
            vendor_match_summary={
                "ranked_vendor_count": len(vendor_rankings),
                "top_vendor_id": vendor_rankings[0].vendor_id if vendor_rankings else None,
                "strategy_gates": strategy_gates,
            },
            fx_context=fx_context,
            freight_context=freight_context,
            phase2a_summary={
                "lines_with_phase2a": sum(1 for line in line_outputs if (line.get("evidence_summary") or {}).get("phase2a_used")),
                "strategy_gates": strategy_gates,
                "uncertain_line_count": sum(
                    1
                    for line in line_outputs
                    if any(((line.get("evidence_summary") or {}).get("phase2a_uncertainty_flags") or {}).values())
                ),
            },
            notes=[
                "Phase 2A recommendation remains additive to Phase 1 seeded-vendor scoring.",
                "Analyzer remains stateless; persistence and recommendation ownership remain in platform-api.",
            ],
        )

    def _persist_outputs(
        self,
        *,
        db: Session,
        project: Project,
        bom: BOM,
        recommendation: ProjectRecommendationResponse,
        actor_id: str | None,
        trace_id: str | None,
        weight_profile: str,
    ) -> None:
        project.average_cost = _as_decimal(recommendation.summary.estimated_project_total)
        project.cost_range_low = _as_decimal(recommendation.summary.cost_range_low)
        project.cost_range_high = _as_decimal(recommendation.summary.cost_range_high)
        project.lead_time_days = _as_decimal(recommendation.summary.estimated_lead_time_days)
        project.decision_summary = recommendation.summary.rationale
        project.analyzer_report = recommendation.model_dump(mode="json")
        project.strategy = {
            "phase": "phase2a_batch4",
            "recommended_vendor_id": recommendation.summary.recommended_vendor_id,
            "recommended_vendor_name": recommendation.summary.recommended_vendor_name,
            "confidence": recommendation.summary.confidence,
            "strategy_gate": recommendation.summary.strategy_gate,
            "freshness": recommendation.freshness.model_dump(),
        }
        project.total_parts = recommendation.summary.total_lines
        project.bom_line_count = recommendation.summary.total_lines
        project.status = ProjectStatus.ANALYSIS_COMPLETE

        analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
        if analysis is None:
            analysis = AnalysisResult(
                bom_id=bom.id,
                user_id=project.user_id,
                guest_session_id=project.guest_session_id,
                project_id=project.id,
                organization_id=project.organization_id,
            )
            db.add(analysis)

        analysis.algorithm_version = self.ANALYSIS_VERSION
        analysis.report_json = recommendation.model_dump(mode="json")
        analysis.summary_json = recommendation.summary.model_dump()
        analysis.strategy_json = project.strategy
        analysis.scoring_json = {
            "vendor_rankings": [vendor.model_dump() for vendor in recommendation.vendor_rankings],
            "freshness": recommendation.freshness.model_dump(),
            "phase2a_summary": recommendation.evidence.phase2a_summary,
        }

        match_run = VendorMatchRun(
            project_id=project.id,
            organization_id=project.organization_id,
            user_id=project.user_id,
            weight_profile=weight_profile,
            filters_json={
                "target_currency": recommendation.summary.target_currency,
                "phase": "phase2a_batch4",
                "strategy_gate": recommendation.summary.strategy_gate,
            },
            weights_json={},
            summary_json=recommendation.summary.model_dump(),
            total_vendors_considered=len(recommendation.vendor_rankings),
            total_matches=len(recommendation.vendor_rankings),
        )
        db.add(match_run)
        db.flush()

        for vendor_row in recommendation.vendor_rankings:
            db.add(
                VendorMatch(
                    match_run_id=match_run.id,
                    project_id=project.id,
                    vendor_id=vendor_row.vendor_id,
                    rank=vendor_row.rank,
                    score=_as_decimal(vendor_row.score),
                    score_breakdown=vendor_row.score_breakdown,
                    explanation=vendor_row.rationale,
                    explanation_json={
                        "confidence": vendor_row.confidence,
                        "confidence_score": vendor_row.confidence_score,
                        "freshness_status": vendor_row.freshness_status,
                    },
                    shortlist_status="shortlisted" if vendor_row.rank <= 5 else "considered",
                    is_primary=vendor_row.rank == 1,
                    elimination_reasons=[],
                    confidence_level=vendor_row.confidence,
                    evidence_json=vendor_row.evidence,
                )
            )

        report_run_id = str(uuid.uuid4())
        db.add(
            ReportSnapshot(
                report_type="procurement_recommendation",
                scope_type="project",
                scope_id=project.id,
                organization_id=project.organization_id,
                report_run_id=report_run_id,
                snapshot_date=date.today(),
                generated_by="on_demand",
                version=1,
                filters_json={
                    "phase": "phase2a_batch4",
                    "target_currency": recommendation.summary.target_currency,
                },
                data_json=recommendation.model_dump(mode="json"),
                summary_json=recommendation.summary.model_dump(),
            )
        )

        db.add(
            EventAuditLog(
                event_type="phase2a_batch4_recommendation_generated",
                entity_type="project",
                entity_id=project.id,
                actor_id=actor_id,
                actor_type="USER" if actor_id else "SYSTEM",
                from_state=str(ProjectStatus.ANALYSIS_IN_PROGRESS),
                to_state=str(ProjectStatus.ANALYSIS_COMPLETE),
                payload={
                    "recommended_vendor_id": recommendation.summary.recommended_vendor_id,
                    "recommended_vendor_name": recommendation.summary.recommended_vendor_name,
                    "confidence": recommendation.summary.confidence,
                    "strategy_gate": recommendation.summary.strategy_gate,
                },
                trace_id=trace_id,
                organization_id=project.organization_id,
            )
        )

    def _audit_failure(
        self,
        *,
        db: Session,
        project: Project,
        actor_id: str | None,
        trace_id: str | None,
        error: str,
    ) -> None:
        db.add(
            EventAuditLog(
                event_type="phase2a_batch4_recommendation_failed",
                entity_type="project",
                entity_id=project.id,
                actor_id=actor_id,
                actor_type="USER" if actor_id else "SYSTEM",
                payload={"error": error[:1000]},
                trace_id=trace_id,
                organization_id=project.organization_id,
            )
        )

    def _estimate_unit_price_source(self, vendor: Vendor, normalized: dict[str, Any]) -> float:
        metadata = vendor.metadata_ or {}
        commercial = vendor.commercial_terms_json or {}

        category = str(normalized.get("procurement_class") or "").lower()
        material = str(normalized.get("material") or "").lower()

        category_map = metadata.get("category_unit_prices") or commercial.get("category_unit_prices") or {}
        if isinstance(category_map, dict):
            for key, value in category_map.items():
                if str(key).lower() == category:
                    return max(_as_float(value, 10.0), 0.01)

        material_map = metadata.get("material_unit_prices") or commercial.get("material_unit_prices") or {}
        if isinstance(material_map, dict):
            for key, value in material_map.items():
                if material and str(key).lower() == material:
                    return max(_as_float(value, 10.0), 0.01)

        return max(
            _as_float(metadata.get("typical_unit_price"), 0.0)
            or _as_float(commercial.get("typical_unit_price"), 0.0)
            or 10.0,
            0.01,
        )

    def _estimate_line_weight_kg(self, normalized: dict[str, Any], quantity: float) -> float:
        specs = normalized.get("specs") or {}
        per_unit = (
            _as_float(specs.get("weight_kg"), 0.0)
            or _as_float(specs.get("estimated_weight_kg"), 0.0)
            or 0.25
        )
        return max(per_unit * quantity, 0.01)

    def _build_fx_context(self, db: Session, target_currency: str) -> dict[str, Any]:
        if target_currency == "USD":
            return {
                "fx_rate": 1.0,
                "fx_source": "identity",
                "fx_timestamp": _now().isoformat(),
                "fx_age_days": 0,
                "overall_status": "fresh",
            }

        row = (
            db.query(FXRate)
            .filter(FXRate.quote_currency == target_currency)
            .order_by(FXRate.fetched_at.desc().nullslast(), FXRate.created_at.desc())
            .first()
        )
        rate = _as_float(row.rate, 1.0) if row else 1.0
        freshness = "fresh"
        if row and row.freshness_status:
            freshness = str(row.freshness_status).lower()
        elif row is None:
            freshness = "stale"

        fetched_at = row.fetched_at if row and row.fetched_at else row.created_at if row else _now()
        age_days = max((_now() - fetched_at).days, 0) if fetched_at else 0

        return {
            "fx_rate": rate,
            "fx_source": row.source if row and row.source else "seed_or_cached",
            "fx_timestamp": fetched_at.isoformat() if fetched_at else None,
            "fx_age_days": age_days,
            "overall_status": freshness,
        }

    def _build_freight_context(
        self,
        *,
        db: Session,
        target_currency: str,
        delivery_location: str,
    ) -> dict[str, Any]:
        query = db.query(FreightRate)
        if delivery_location:
            freight = (
                query.filter(FreightRate.destination_region.ilike(f"%{delivery_location}%"))
                .order_by(FreightRate.effective_from.desc(), FreightRate.created_at.desc())
                .first()
            )
        else:
            freight = query.order_by(FreightRate.effective_from.desc(), FreightRate.created_at.desc()).first()

        if not freight:
            return {
                "freight_mode": "baseline",
                "freight_currency": target_currency,
                "freight_rate_per_kg": 0.0,
                "freight_min_charge": 0.0,
                "overall_status": "stale",
            }

        freight_currency = _upper_or(target_currency, freight.currency)
        convert_rate = 1.0
        if freight_currency != target_currency:
            try:
                convert_rate = _as_float(
                    fx_service.get_rate(
                        db,
                        base_currency=freight_currency,
                        quote_currency=target_currency,
                    ),
                    1.0,
                )
            except Exception:
                convert_rate = 1.0

        freshness = str(freight.freshness_status).lower() if freight.freshness_status else "fresh"
        return {
            "freight_mode": freight.mode or "baseline",
            "freight_currency": target_currency,
            "freight_rate_per_kg": round(_as_float(freight.rate_per_kg) * convert_rate, 6) if freight.rate_per_kg is not None else None,
            "freight_min_charge": round(_as_float(freight.min_charge) * convert_rate, 6) if freight.min_charge is not None else None,
            "overall_status": freshness,
        }

    def _evidence_weighted_confidence_score(
        self,
        *,
        normalized_confidence: float,
        freshness_status: str,
        has_full_match: bool,
        phase2a_bundle: dict[str, Any] | None = None,
    ) -> float:
        phase2a_bundle = phase2a_bundle or {}
        confidence_summary = phase2a_bundle.get("confidence_summary") or {}
        uncertainty_flags = phase2a_bundle.get("uncertainty_flags") or {}
        phase2a_score = _as_float(confidence_summary.get("score"), normalized_confidence)
        completeness = 1.0
        if uncertainty_flags:
            completeness = max(
                0.0,
                1.0 - (sum(1 for value in uncertainty_flags.values() if value) / max(len(uncertainty_flags), 1)),
            )

        freshness_factor = 1.0
        lowered_freshness = str(freshness_status or "fresh").lower()
        if lowered_freshness in {"recent", "mixed"}:
            freshness_factor = 0.78
        elif lowered_freshness in {"stale", "unknown", "uncertain"}:
            freshness_factor = 0.58
        elif lowered_freshness in {"expired", "missing"}:
            freshness_factor = 0.35

        score = (
            (normalized_confidence * 0.35)
            + (phase2a_score * 0.35)
            + (completeness * 0.20)
            + ((1.0 if has_full_match else 0.45) * 0.10)
        ) * freshness_factor

        critical_missing = sum(
            1
            for key in ("offer_missing", "availability_missing", "tariff_uncertain", "freight_uncertain", "hs_uncertain")
            if uncertainty_flags.get(key)
        )
        score -= critical_missing * 0.04
        return max(0.0, min(1.0, score))

    def _confidence_label(self, *, confidence_score: float) -> str:
        if confidence_score >= 0.80:
            return "HIGH"
        if confidence_score >= 0.50:
            return "MEDIUM"
        return "LOW"

    def _phase2a_strategy_gate(
        self,
        *,
        phase2a_bundle: dict[str, Any] | None,
        has_recommendation: bool,
    ) -> dict[str, Any]:
        bundle = phase2a_bundle or {}
        uncertainty_flags = bundle.get("uncertainty_flags") or {}
        freshness_summary = bundle.get("freshness_summary") or {}
        reasons: list[str] = []

        if not has_recommendation:
            reasons.append("No matched seeded vendor was available for this BOM line.")

        if uncertainty_flags.get("offer_missing"):
            reasons.append("Offer evidence is missing.")
        if uncertainty_flags.get("availability_missing"):
            reasons.append("Availability evidence is missing or unknown.")
        if uncertainty_flags.get("hs_uncertain"):
            reasons.append("HS mapping is low-confidence or unresolved.")
        if uncertainty_flags.get("tariff_uncertain"):
            reasons.append("Tariff evidence is uncertain.")
        if uncertainty_flags.get("freight_uncertain"):
            reasons.append("Freight evidence is uncertain.")

        freshness_status = str(freshness_summary.get("status") or "unknown").lower()
        if freshness_status in {"stale", "mixed", "expired"}:
            reasons.append(f"Phase 2A evidence freshness is {freshness_status}.")

        if not has_recommendation or uncertainty_flags.get("offer_missing"):
            gate = "rfq-first"
        elif any(
            uncertainty_flags.get(key)
            for key in ("availability_missing", "hs_uncertain", "tariff_uncertain", "freight_uncertain")
        ) or freshness_status in {"stale", "mixed", "expired"}:
            gate = "verify-first"
        else:
            gate = "award-ready"

        return {"strategy_gate": gate, "strategy_reasons": reasons}

    def _collapse_status(self, statuses: list[str]) -> str:
        lowered = [str(s).lower() for s in statuses if s]
        if not lowered:
            return "fresh"
        if "stale" in lowered or "expired" in lowered:
            return "stale"
        return "fresh"


runtime_pipeline_service = RuntimePipelineService()