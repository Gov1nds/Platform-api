from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.outcomes import AnomalyFlag, OverrideEvent, VendorPerformance


@dataclass
class OutcomeScoreAdjustment:
    score_adjustment: float
    confidence_adjustment: float
    lead_time_days: float | None
    lead_time_confidence_adjustment: float
    strategy_gate_bias: str | None
    performance_adjustment: dict[str, Any]
    override_adjustment: dict[str, Any]
    anomaly_adjustment: dict[str, Any]
    explanation_fragments: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_adjustment": round(self.score_adjustment, 4),
            "confidence_adjustment": round(self.confidence_adjustment, 4),
            "lead_time_days": round(self.lead_time_days, 2) if self.lead_time_days is not None else None,
            "lead_time_confidence_adjustment": round(self.lead_time_confidence_adjustment, 4),
            "strategy_gate_bias": self.strategy_gate_bias,
            "performance_adjustment": self.performance_adjustment,
            "override_adjustment": self.override_adjustment,
            "anomaly_adjustment": self.anomaly_adjustment,
            "explanation_fragments": list(self.explanation_fragments),
        }


class OutcomeInformedScoringService:
    """Deterministic bounded Phase 2C.5 scoring adjustments."""

    performance_cap = 0.08
    override_cap = 0.05
    anomaly_cap = 0.08
    total_score_cap = 0.18
    total_confidence_cap = 0.15

    def _as_float(self, value: Any, default: float | None = None) -> float | None:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    def _bounded(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _latest_vendor_performance(self, db: Session, vendor_id: str) -> VendorPerformance | None:
        return (
            db.query(VendorPerformance)
            .filter(VendorPerformance.vendor_id == vendor_id)
            .order_by(VendorPerformance.period_end.desc(), VendorPerformance.created_at.desc())
            .first()
        )

    def _similarity_keys(self, part: BOMPart | None) -> set[str]:
        keys: set[str] = set()
        if not part:
            return keys
        for value in (
            part.procurement_class,
            part.category_code,
            part.material,
            part.canonical_part_key,
        ):
            if value:
                keys.add(str(value).strip().lower())
        return keys

    def _override_penalty(
        self,
        db: Session,
        *,
        vendor_id: str,
        bom_line_id: str,
    ) -> tuple[float, dict[str, Any], list[str]]:
        part = db.query(BOMPart).filter(BOMPart.id == bom_line_id).first()
        similarity_keys = self._similarity_keys(part)
        rows = (
            db.query(OverrideEvent, BOMPart)
            .outerjoin(BOMPart, BOMPart.id == OverrideEvent.bom_line_id)
            .filter(OverrideEvent.recommended_vendor_id == vendor_id)
            .order_by(OverrideEvent.timestamp.desc())
            .limit(200)
            .all()
        )
        if not rows:
            return 0.0, {"sample_size": 0, "override_rate": 0.0, "similarity_matches": 0}, []

        relevant = []
        for event, override_part in rows:
            override_keys = self._similarity_keys(override_part)
            if not similarity_keys or not override_keys or similarity_keys & override_keys:
                relevant.append(event)

        sample_size = len(relevant)
        if sample_size < 2:
            return 0.0, {"sample_size": sample_size, "override_rate": 0.0, "similarity_matches": sample_size}, []

        distinct_chosen = sum(1 for row in relevant if row.chosen_vendor_id and row.chosen_vendor_id != vendor_id)
        override_rate = distinct_chosen / max(sample_size, 1)
        if sample_size < 3 or override_rate < 0.6:
            return 0.0, {
                "sample_size": sample_size,
                "override_rate": round(override_rate, 4),
                "similarity_matches": sample_size,
            }, []

        raw_penalty = min(self.override_cap, (override_rate - 0.5) * 0.10)
        fragments = [f"Override history penalty applied from {sample_size} similar overrides."]
        return -raw_penalty, {
            "sample_size": sample_size,
            "override_rate": round(override_rate, 4),
            "similarity_matches": sample_size,
            "distinct_chosen_count": distinct_chosen,
        }, fragments

    def _anomaly_adjustment(self, anomaly_summary: dict[str, Any]) -> tuple[float, float, dict[str, Any], list[str], str | None]:
        price = anomaly_summary.get("price") or {}
        lead = anomaly_summary.get("lead_time") or {}
        availability = anomaly_summary.get("availability") or {}
        counts = {
            "price_count": int(price.get("count") or 0),
            "lead_time_count": int(lead.get("count") or 0),
            "availability_count": int(availability.get("count") or 0),
            "has_high_severity": bool(anomaly_summary.get("has_high_severity")),
        }
        score_penalty = 0.0
        confidence_penalty = 0.0
        strategy_gate_bias = None
        fragments: list[str] = []

        if counts["price_count"]:
            price_penalty = min(0.03, 0.015 * counts["price_count"])
            score_penalty += price_penalty
            fragments.append("Price anomaly damping reduced price influence.")
        else:
            price_penalty = 0.0

        if counts["lead_time_count"]:
            lead_penalty = min(0.035, 0.0175 * counts["lead_time_count"])
            score_penalty += lead_penalty
            confidence_penalty += min(0.04, 0.02 * counts["lead_time_count"])
            fragments.append("Lead-time anomaly damping reduced delivery trust.")
        else:
            lead_penalty = 0.0

        if counts["availability_count"]:
            availability_penalty = min(0.04, 0.02 * counts["availability_count"])
            score_penalty += availability_penalty
            confidence_penalty += min(0.05, 0.025 * counts["availability_count"])
            strategy_gate_bias = "rfq-first"
            fragments.append("Availability anomaly suggests RFQ-first verification.")
        else:
            availability_penalty = 0.0

        total_score_penalty = min(self.anomaly_cap, score_penalty)
        total_confidence_penalty = min(self.anomaly_cap, confidence_penalty)
        return (
            -total_score_penalty,
            -total_confidence_penalty,
            {
                **counts,
                "price_penalty": round(price_penalty, 4),
                "lead_time_penalty": round(lead_penalty, 4),
                "availability_penalty": round(availability_penalty, 4),
            },
            fragments,
            strategy_gate_bias,
        )

    def _performance_adjustment(self, performance: VendorPerformance | None) -> tuple[float, float, dict[str, Any], list[str]]:
        if not performance:
            return 0.0, 0.0, {"available": False}, []

        metadata = performance.source_metadata or {}
        quote_count = int(metadata.get("quote_outcome_count") or 0)
        lead_count = int(metadata.get("lead_time_history_count") or 0)
        issue_rate = self._as_float(metadata.get("issue_rate"), 0.0) or 0.0
        sample_size = max(quote_count, lead_count)
        if sample_size < 2:
            return 0.0, 0.0, {
                "available": True,
                "sample_size": sample_size,
                "quote_outcome_count": quote_count,
                "lead_time_history_count": lead_count,
                "issue_rate": round(issue_rate, 4),
            }, []

        on_time_rate = self._as_float(performance.on_time_rate, 0.0) or 0.0
        win_rate = self._as_float(performance.po_win_rate, 0.0) or 0.0
        lead_variance = self._as_float(performance.lead_time_variance, 0.0) or 0.0
        price_variance = abs(self._as_float(performance.price_variance, 0.0) or 0.0)

        score = 0.0
        confidence = 0.0
        fragments: list[str] = []

        if on_time_rate >= 0.85:
            score += 0.03
            confidence += 0.04
            fragments.append("Strong on-time performance improved lead-time trust.")
        elif on_time_rate <= 0.5:
            score -= 0.04
            confidence -= 0.06
            fragments.append("Low on-time performance reduced lead-time trust.")

        if win_rate >= 0.6:
            score += 0.015
        elif win_rate and win_rate <= 0.25:
            score -= 0.015

        if lead_variance >= 16:
            score -= 0.02
            confidence -= 0.02
            fragments.append("High lead-time variance reduced delivery confidence.")
        elif 0 < lead_variance <= 4:
            score += 0.01
            confidence += 0.01

        if price_variance >= 5:
            score -= 0.01

        if issue_rate >= 0.2:
            score -= 0.02
            confidence -= 0.02
            fragments.append("Issue history added a conservative quality penalty.")

        score = self._bounded(score, -self.performance_cap, self.performance_cap)
        confidence = self._bounded(confidence, -self.total_confidence_cap, self.total_confidence_cap)
        return score, confidence, {
            "available": True,
            "sample_size": sample_size,
            "quote_outcome_count": quote_count,
            "lead_time_history_count": lead_count,
            "on_time_rate": round(on_time_rate, 4),
            "po_win_rate": round(win_rate, 4),
            "lead_time_variance": round(lead_variance, 4),
            "price_variance": round(price_variance, 4),
            "issue_rate": round(issue_rate, 4),
        }, fragments

    def build_adjustment(
        self,
        db: Session,
        *,
        vendor_id: str,
        bom_line_id: str,
        anomaly_summary: dict[str, Any] | None = None,
        adjusted_lead_time_days: Decimal | float | None = None,
    ) -> OutcomeScoreAdjustment:
        performance = self._latest_vendor_performance(db, vendor_id)
        perf_score, perf_conf, perf_meta, perf_fragments = self._performance_adjustment(performance)
        override_penalty, override_meta, override_fragments = self._override_penalty(
            db,
            vendor_id=vendor_id,
            bom_line_id=bom_line_id,
        )
        anomaly_score, anomaly_conf, anomaly_meta, anomaly_fragments, strategy_gate_bias = self._anomaly_adjustment(anomaly_summary or {})

        score_adjustment = self._bounded(perf_score + override_penalty + anomaly_score, -self.total_score_cap, self.total_score_cap)
        confidence_adjustment = self._bounded(perf_conf + anomaly_conf, -self.total_confidence_cap, self.total_confidence_cap)
        explanation_fragments = [
            *perf_fragments,
            *override_fragments,
            *anomaly_fragments,
        ]
        return OutcomeScoreAdjustment(
            score_adjustment=score_adjustment,
            confidence_adjustment=confidence_adjustment,
            lead_time_days=self._as_float(adjusted_lead_time_days),
            lead_time_confidence_adjustment=perf_conf,
            strategy_gate_bias=strategy_gate_bias,
            performance_adjustment=perf_meta,
            override_adjustment=override_meta,
            anomaly_adjustment=anomaly_meta,
            explanation_fragments=explanation_fragments,
        )


outcome_informed_scoring_service = OutcomeInformedScoringService()