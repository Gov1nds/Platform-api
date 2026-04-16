"""
Decision Safety Service.

Implements Execution Plan §7 (Decision Safety Rules). Evaluates each
strategy's top_option + runner-ups against eight safety rules. Outputs
DecisionSafetyReport with overall_safe, human_review_required, list of
vendor IDs that require an RFQ, per-vendor risk flags, and a narrative
summary suitable for UI display.

Rules implemented:
  R1  missing_critical_profile_data  — UNVERIFIED tier / missing required fields
  R2  no_performance_history         — evidence_count < 2
  R3  anomaly_penalty                — high/critical anomalies force RFQ
  R4  weak_match_gate                — part-vendor match_score < 0.50
  R5  ambiguous_top_vendors          — top score tie within 0.08
  R6  suspiciously_low_price         — perfect price score + near_zero_price anomaly
  R7  chronic_delivery_failures      — on-time < 60 % over ≥ 3 POs
  R8  requires_human_review          — confidence score < 0.40
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


R_MISSING = "missing_critical_profile_data"
R_NO_HISTORY = "no_performance_history"
R_ANOMALY = "anomaly_penalty"
R_WEAK_MATCH = "weak_match_gate"
R_AMBIGUOUS = "ambiguous_top_vendors"
R_LOW_PRICE = "suspiciously_low_price_detected"
R_CHRONIC = "chronic_delivery_failures"
R_HUMAN_REVIEW = "requires_human_review"


@dataclass
class DecisionSafetyReport:
    overall_safe: bool
    human_review_required: bool
    rfq_required_vendors: list[str] = field(default_factory=list)
    risk_flags: dict[str, list[str]] = field(default_factory=dict)
    strategy_issues: dict[str, list[str]] = field(default_factory=dict)
    safety_narrative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_safe": bool(self.overall_safe),
            "human_review_required": bool(self.human_review_required),
            "rfq_required_vendors": list(self.rfq_required_vendors),
            "risk_flags": {k: list(v) for k, v in self.risk_flags.items()},
            "strategy_issues": {k: list(v) for k, v in self.strategy_issues.items()},
            "safety_narrative": self.safety_narrative,
        }


class DecisionSafetyService:
    """Apply the 8 Phase-3 decision-safety rules to generated strategies."""

    def evaluate_recommendation_safety(
        self,
        strategy_results: Iterable[Any],
        vendor_scores: dict[str, dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> DecisionSafetyReport:
        """
        Parameters
        ----------
        strategy_results : iterable of SourcingStrategy objects (or dicts with
            the same shape: strategy_name, top_option, runner_up_options,
            strategy_confidence, rfq_required).
        vendor_scores : optional map vendor_id → {trust_tier, evidence_count,
            on_time_delivery_pct, total_pos, match_score, anomaly_flags,
            price_competitiveness_score, confidence_score}.
        evidence : optional free-form evidence bundle passed through.
        """
        vendor_scores = vendor_scores or {}
        risk_flags: dict[str, list[str]] = {}
        rfq_required_vendors: set[str] = set()
        strategy_issues: dict[str, list[str]] = {}
        human_review = False

        for strategy in strategy_results:
            s_name, top, runners, s_conf, s_rfq = self._unpack_strategy(strategy)
            issues: list[str] = []

            if top is None:
                issues.append("no_eligible_vendors")
                strategy_issues[s_name] = issues
                continue

            vendor_id = self._get(top, "vendor_id")
            tier = (self._vs(vendor_scores, vendor_id, "trust_tier") or "UNVERIFIED").upper()
            ev_count = int(self._vs(vendor_scores, vendor_id, "evidence_count") or 0)
            total_pos = int(self._vs(vendor_scores, vendor_id, "total_pos") or 0)
            on_time = float(self._vs(vendor_scores, vendor_id, "on_time_delivery_pct") or 0.0)
            match_score = float(self._vs(vendor_scores, vendor_id, "match_score") or 0.0)
            price_score = float(self._vs(vendor_scores, vendor_id, "price_competitiveness_score") or 0.0)
            conf_score = float(self._get(top, "confidence_score") or 0.0)
            anomalies = self._vs(vendor_scores, vendor_id, "anomaly_flags") or []

            flags = risk_flags.setdefault(vendor_id, [])

            # R1 — Missing critical profile data
            if tier == "UNVERIFIED" or bool(self._vs(vendor_scores, vendor_id, "missing_required_fields")):
                flags.append(R_MISSING)
                rfq_required_vendors.add(vendor_id)

            # R2 — No performance history
            if total_pos < 2 or ev_count == 0:
                flags.append(R_NO_HISTORY)
                rfq_required_vendors.add(vendor_id)

            # R3 — Anomaly severity gates
            has_high_severity = any(
                (a.get("severity") if isinstance(a, dict) else getattr(a, "severity", ""))
                in ("HIGH", "CRITICAL")
                for a in anomalies
            )
            if has_high_severity:
                flags.append(R_ANOMALY)
                rfq_required_vendors.add(vendor_id)

            # R4 — Weak match gate
            if 0 < match_score < 0.50:
                flags.append(R_WEAK_MATCH)
                rfq_required_vendors.add(vendor_id)
            # Also preserve matcher's own rfq_recommended signal
            if bool(self._get(top, "rfq_recommended")):
                rfq_required_vendors.add(vendor_id)

            # R5 — Ambiguous top: top vs runner-up within 0.08
            if runners:
                second_score = float(self._get(runners[0], "strategy_score") or 0.0)
                top_score = float(self._get(top, "strategy_score") or 0.0)
                if top_score > 0 and abs(top_score - second_score) < 0.08:
                    flags.append(R_AMBIGUOUS)
                    strategy_issues.setdefault(s_name, []).append(R_AMBIGUOUS)

            # R6 — Suspiciously low price
            low_price_anomaly = any(
                (a.get("anomaly_type") if isinstance(a, dict) else getattr(a, "anomaly_type", ""))
                == "near_zero_price"
                for a in anomalies
            )
            if low_price_anomaly and price_score >= 0.99:
                flags.append(R_LOW_PRICE)
                rfq_required_vendors.add(vendor_id)
                human_review = True

            # R7 — Chronic underperformer
            if total_pos >= 3 and on_time > 0 and on_time < 0.60:
                flags.append(R_CHRONIC)
                rfq_required_vendors.add(vendor_id)

            # R8 — Confidence threshold for human review
            if conf_score < 0.40:
                flags.append(R_HUMAN_REVIEW)
                human_review = True

            # Propagate into strategy_issues
            for rule in flags:
                strategy_issues.setdefault(s_name, []).append(f"{rule}@{vendor_id}")

        # Overall safety
        overall_safe = not human_review and not any(
            R_LOW_PRICE in flags or R_MISSING in flags
            for flags in risk_flags.values()
        )

        narrative = self._build_narrative(risk_flags, human_review, len(rfq_required_vendors))

        report = DecisionSafetyReport(
            overall_safe=overall_safe,
            human_review_required=human_review,
            rfq_required_vendors=sorted(rfq_required_vendors),
            risk_flags={k: list(set(v)) for k, v in risk_flags.items()},
            strategy_issues={k: list(set(v)) for k, v in strategy_issues.items()},
            safety_narrative=narrative,
        )
        logger.info(
            "decision_safety overall_safe=%s hr=%s rfq_vendors=%d",
            report.overall_safe, report.human_review_required, len(report.rfq_required_vendors),
        )
        return report

    # ── Helpers ──────────────────────────────────────────────────────────

    def _unpack_strategy(self, strategy: Any):
        if isinstance(strategy, dict):
            return (
                strategy.get("strategy_name"),
                strategy.get("top_option"),
                strategy.get("runner_up_options") or [],
                strategy.get("strategy_confidence"),
                strategy.get("rfq_required"),
            )
        return (
            getattr(strategy, "strategy_name", ""),
            getattr(strategy, "top_option", None),
            getattr(strategy, "runner_up_options", []) or [],
            getattr(strategy, "strategy_confidence", None),
            getattr(strategy, "rfq_required", False),
        )

    def _get(self, obj: Any, key: str):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _vs(self, vendor_scores: dict[str, dict[str, Any]], vendor_id: str | None, key: str):
        if not vendor_id:
            return None
        return (vendor_scores.get(vendor_id) or {}).get(key)

    def _build_narrative(
        self,
        risk_flags: dict[str, list[str]],
        human_review: bool,
        rfq_vendor_count: int,
    ) -> str:
        if not risk_flags and not human_review:
            return "All recommendations passed decision-safety checks."
        parts: list[str] = []
        if human_review:
            parts.append("Human review required before auto-award.")
        if rfq_vendor_count:
            parts.append(f"{rfq_vendor_count} vendor(s) flagged for RFQ-first treatment.")
        # Most common failure types
        counts: dict[str, int] = {}
        for flags in risk_flags.values():
            for f in flags:
                counts[f] = counts.get(f, 0) + 1
        if counts:
            top_rule = max(counts.items(), key=lambda x: x[1])
            parts.append(f"Most common flag: {top_rule[0]} ({top_rule[1]} vendor(s)).")
        return " ".join(parts)


decision_safety_service = DecisionSafetyService()
