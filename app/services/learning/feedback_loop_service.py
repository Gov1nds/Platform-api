"""
Continuous Learning / Feedback-Loop service.

Implements Execution Plan §9: learn from real outcomes, track override
patterns, auto-normalize vendor aliases, guard against bad learning with
evidence thresholds, and require human-in-the-loop for significant changes.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.feedback import LearningEvent, RecommendationOverride
from app.models.matching import PartVendorIndex
from app.models.vendor import (
    Vendor, VendorIdentityAlias, VendorImportRow,
)
from app.services.matching.part_vendor_matcher import part_vendor_matcher

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheck:
    approved: bool
    capped_adjustment: float
    human_review_required: bool
    reason: str


@dataclass
class OverridePatternAnalysis:
    vendor_id: str
    total_recommendations: int
    total_overrides: int
    override_rate: float
    investigation_required: bool
    by_category: dict[str, int] = field(default_factory=dict)
    recent_overrides: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "total_recommendations": self.total_recommendations,
            "total_overrides": self.total_overrides,
            "override_rate": round(self.override_rate, 4),
            "investigation_required": bool(self.investigation_required),
            "by_category": dict(self.by_category),
            "recent_overrides": list(self.recent_overrides),
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


class FeedbackLoopService:
    """§9 continuous learning service."""

    # ── User override capture ────────────────────────────────────────────

    def record_user_override(
        self,
        project_id: str,
        bom_part_id: str | None,
        canonical_part_key: str | None,
        recommended_vendor_id: str | None,
        override_vendor_id: str | None,
        override_reason: str | None,
        override_by_user_id: str | None,
        db: Session,
        strategy_at_time: str | None = None,
        score_at_time: float | Decimal | None = None,
        override_metadata: dict[str, Any] | None = None,
    ) -> RecommendationOverride:
        rec = RecommendationOverride(
            project_id=project_id,
            bom_part_id=bom_part_id,
            canonical_part_key=canonical_part_key,
            recommended_vendor_id=recommended_vendor_id,
            override_vendor_id=override_vendor_id,
            override_reason=override_reason,
            override_by_user_id=override_by_user_id,
            strategy_at_time=strategy_at_time,
            score_at_time=Decimal(str(score_at_time)) if score_at_time is not None else None,
            override_metadata=override_metadata or {},
        )
        db.add(rec)
        db.flush()

        # Feed back into part-vendor-index (boost the overridden vendor's signal)
        if canonical_part_key and override_vendor_id:
            try:
                part_vendor_matcher.update_index_from_outcome(
                    canonical_part_key=canonical_part_key,
                    vendor_id=override_vendor_id,
                    outcome_type="rfq_response",
                    outcome_data={"source": "user_override", "reason": override_reason},
                    db=db,
                )
            except Exception:
                logger.exception("update_index_from_outcome failed after override")

        # Check override pattern for the recommended vendor
        if recommended_vendor_id:
            analysis = self.get_override_pattern_analysis(recommended_vendor_id, db)
            if analysis.investigation_required:
                db.add(
                    LearningEvent(
                        event_type="override_pattern_investigation",
                        vendor_id=recommended_vendor_id,
                        canonical_part_key=canonical_part_key,
                        trigger="user_override",
                        old_value={
                            "override_rate": analysis.override_rate,
                            "total_recommendations": analysis.total_recommendations,
                        },
                        new_value=None,
                        evidence_count_at_time=analysis.total_recommendations,
                        human_review_required=True,
                        notes="override rate exceeds investigation threshold",
                    )
                )

        # Simple learning event for the override itself
        db.add(
            LearningEvent(
                event_type="user_override",
                vendor_id=override_vendor_id or recommended_vendor_id,
                canonical_part_key=canonical_part_key,
                trigger="user_override",
                old_value={"recommended_vendor_id": recommended_vendor_id},
                new_value={"override_vendor_id": override_vendor_id, "reason": override_reason},
                evidence_count_at_time=None,
                human_review_required=False,
                notes=override_reason,
            )
        )
        logger.info(
            "record_user_override project=%s recommended=%s override=%s",
            project_id, recommended_vendor_id, override_vendor_id,
        )
        return rec

    # ── RFQ outcome processing ───────────────────────────────────────────

    def process_rfq_result(
        self,
        rfq_id: str,
        outcomes: list[dict[str, Any]],
        db: Session,
    ) -> dict[str, Any]:
        """
        outcomes: list of dicts per vendor participation.
        Each dict keys: vendor_id, canonical_part_key, responded (bool),
          quoted_price, currency, quote_date, quoted_lead_time_days,
          won (bool), po_date.
        """
        processed = 0
        for o in outcomes:
            vid = o.get("vendor_id")
            cpk = o.get("canonical_part_key")
            if not vid or not cpk:
                continue
            if o.get("responded"):
                part_vendor_matcher.update_index_from_outcome(
                    canonical_part_key=cpk,
                    vendor_id=vid,
                    outcome_type="rfq_response",
                    outcome_data={
                        "quoted_price": o.get("quoted_price"),
                        "currency": o.get("currency"),
                        "quote_date": o.get("quote_date"),
                        "quoted_lead_time_days": o.get("quoted_lead_time_days"),
                    },
                    db=db,
                )
                processed += 1
            else:
                part_vendor_matcher.update_index_from_outcome(
                    canonical_part_key=cpk,
                    vendor_id=vid,
                    outcome_type="rfq_sent",
                    outcome_data={"source_rfq_id": rfq_id},
                    db=db,
                )
            if o.get("won"):
                part_vendor_matcher.update_index_from_outcome(
                    canonical_part_key=cpk,
                    vendor_id=vid,
                    outcome_type="po_awarded",
                    outcome_data={"po_date": o.get("po_date"), "source_rfq_id": rfq_id},
                    db=db,
                )
        logger.info("process_rfq_result rfq=%s outcomes=%d processed=%d", rfq_id, len(outcomes), processed)
        return {"rfq_id": rfq_id, "processed": processed}

    # ── Alias normalization ──────────────────────────────────────────────

    def normalize_vendor_alias_from_pattern(
        self,
        db: Session,
        min_occurrences: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Scan vendor_import_rows: when the same normalized name appears at
        least `min_occurrences` times resolving to the same vendor fingerprint
        and no VendorIdentityAlias entry exists, auto-create one.
        """
        rows = (
            db.query(VendorImportRow)
            .filter(VendorImportRow.status == "promoted")
            .limit(5000)
            .all()
        )
        grouping: dict[tuple[str, str], list[VendorImportRow]] = {}
        for r in rows:
            ident = r.normalized_identity or {}
            name_norm = _normalize(ident.get("normalized_name") or ident.get("name"))
            vendor_id = r.created_vendor_id or r.matched_vendor_id
            if not name_norm or not vendor_id:
                continue
            grouping.setdefault((vendor_id, name_norm), []).append(r)

        created_aliases: list[dict[str, Any]] = []
        for (vendor_id, normalized_name), members in grouping.items():
            if len(members) < min_occurrences:
                continue
            existing = (
                db.query(VendorIdentityAlias)
                .filter(
                    VendorIdentityAlias.vendor_id == vendor_id,
                    VendorIdentityAlias.alias_type == "imported_trade_name",
                    VendorIdentityAlias.normalized_value == normalized_name,
                )
                .first()
            )
            if existing is not None:
                continue
            alias = VendorIdentityAlias(
                vendor_id=vendor_id,
                alias_type="imported_trade_name",
                alias_value=normalized_name,
                normalized_value=normalized_name,
                confidence=Decimal("0.85"),
                provenance="pattern_learning",
            )
            db.add(alias)
            db.add(
                LearningEvent(
                    event_type="alias_normalized",
                    vendor_id=vendor_id,
                    canonical_part_key=None,
                    trigger="scheduled_recompute",
                    old_value=None,
                    new_value={"alias": normalized_name, "occurrences": len(members)},
                    evidence_count_at_time=len(members),
                    human_review_required=False,
                    notes="auto-created from repeated import name",
                )
            )
            created_aliases.append(
                {"vendor_id": vendor_id, "alias": normalized_name, "occurrences": len(members)}
            )
        logger.info("normalize_vendor_alias_from_pattern created=%d", len(created_aliases))
        return created_aliases

    # ── Safety guardrails ────────────────────────────────────────────────

    def evaluate_score_adjustment_safety(
        self,
        vendor_id: str,
        proposed_adjustment: float,
        evidence_count: int,
        trigger: str,
    ) -> SafetyCheck:
        """
        Guards against bad learning:
        - evidence_count < 3: reject adjustment magnitude > 0.10
        - evidence_count < 5: reject adjustment magnitude > 0.20
        - magnitude > 0.30: ALWAYS require human review
        - trigger == "single_anomaly": cap at 0.10
        """
        magnitude = abs(proposed_adjustment)
        cap = proposed_adjustment

        if trigger == "single_anomaly" and magnitude > 0.10:
            cap = 0.10 if proposed_adjustment > 0 else -0.10
            return SafetyCheck(
                approved=True,
                capped_adjustment=cap,
                human_review_required=False,
                reason="single_anomaly_cap_applied",
            )

        if magnitude > 0.30:
            return SafetyCheck(
                approved=False,
                capped_adjustment=0.0,
                human_review_required=True,
                reason="large_adjustment_requires_review",
            )

        if evidence_count < 3 and magnitude > 0.10:
            return SafetyCheck(
                approved=False,
                capped_adjustment=0.0,
                human_review_required=False,
                reason="insufficient_evidence_for_adjustment_above_0.10",
            )

        if evidence_count < 5 and magnitude > 0.20:
            return SafetyCheck(
                approved=False,
                capped_adjustment=0.0,
                human_review_required=False,
                reason="insufficient_evidence_for_adjustment_above_0.20",
            )

        return SafetyCheck(
            approved=True,
            capped_adjustment=cap,
            human_review_required=False,
            reason="within_safety_thresholds",
        )

    # ── Override pattern analysis ────────────────────────────────────────

    def get_override_pattern_analysis(
        self,
        vendor_id: str,
        db: Session,
        window_days: int = 180,
        investigation_threshold: float = 0.40,
    ) -> OverridePatternAnalysis:
        cutoff = _now() - timedelta(days=window_days)
        overrides = (
            db.query(RecommendationOverride)
            .filter(
                RecommendationOverride.recommended_vendor_id == vendor_id,
                RecommendationOverride.created_at >= cutoff,
            )
            .order_by(RecommendationOverride.created_at.desc())
            .all()
        )

        # Total recommendations proxy: count distinct (project, bom_part) pairs
        # where this vendor appeared as recommended. Since we don't have a
        # "vendor_recommendations" table, we also look at part_vendor_index
        # evidence counts as a proxy for exposure.
        pvi_exposure = (
            db.query(PartVendorIndex)
            .filter(PartVendorIndex.vendor_id == vendor_id)
            .count()
        )
        total_recs = max(len(overrides), pvi_exposure)  # lower bound

        override_rate = (
            len(overrides) / total_recs if total_recs > 0 else 0.0
        )

        by_category = Counter()
        recent: list[dict[str, Any]] = []
        for o in overrides[:20]:
            meta = o.override_metadata or {}
            cat = meta.get("category") or "unknown"
            by_category[cat] += 1
            recent.append(
                {
                    "project_id": o.project_id,
                    "override_vendor_id": o.override_vendor_id,
                    "reason": o.override_reason,
                    "at": o.created_at.isoformat() if o.created_at else None,
                }
            )

        investigation = (
            override_rate >= investigation_threshold
            and len(overrides) >= 3
        )

        return OverridePatternAnalysis(
            vendor_id=vendor_id,
            total_recommendations=total_recs,
            total_overrides=len(overrides),
            override_rate=override_rate,
            investigation_required=investigation,
            by_category=dict(by_category),
            recent_overrides=recent,
        )


feedback_loop_service = FeedbackLoopService()
