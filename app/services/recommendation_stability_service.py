from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.recommendation import VendorRankingEntry


@dataclass
class StabilityDecision:
    candidate_rankings: list[VendorRankingEntry]
    rank_changed: bool
    prior_rank: int | None
    score_delta: float | None
    material_change_flag: bool
    stability_reason: str


class RecommendationStabilityService:
    """Rules-based anti-jitter preservation for line-level recommendations."""

    small_delta_threshold = 0.035
    material_price_delta_threshold = 0.10
    material_freight_delta_threshold = 0.15

    def _as_float(self, value: Any, default: float | None = None) -> float | None:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    def _get_prior_vendor_state(self, previous_line: dict[str, Any] | None, vendor_id: str) -> dict[str, Any] | None:
        if not previous_line:
            return None
        for row in previous_line.get("candidate_rankings") or []:
            if row.get("vendor_id") == vendor_id:
                return row
        return None

    def _relative_delta(self, current: float | None, previous: float | None) -> float | None:
        if current is None or previous in (None, 0):
            return None
        return abs(current - previous) / abs(previous)

    def _availability_probe(self, candidate: VendorRankingEntry | dict[str, Any] | None) -> str | None:
        if not candidate:
            return None
        evidence = candidate.evidence if isinstance(candidate, VendorRankingEntry) else candidate.get("evidence") or {}
        phase2a = evidence.get("phase2a") or {}
        availability = phase2a.get("availability_evidence") or {}
        feasible = availability.get("feasible")
        if feasible is not None:
            return "feasible" if feasible else "not_feasible"
        status = availability.get("availability_status") or availability.get("status")
        return str(status).lower() if status is not None else None

    def _material_change(self, current_prev_vendor: VendorRankingEntry, previous_prev_vendor: dict[str, Any] | None) -> tuple[bool, str]:
        if not previous_prev_vendor:
            return False, "no_prior_candidate_state"

        curr_price = self._as_float(current_prev_vendor.estimated_line_total)
        prev_price = self._as_float(previous_prev_vendor.get("estimated_line_total"))
        price_delta = self._relative_delta(curr_price, prev_price)
        if price_delta is not None and price_delta >= self.material_price_delta_threshold:
            return True, "material_price_change"

        curr_freight = self._as_float(current_prev_vendor.estimated_freight_total)
        prev_freight = self._as_float(previous_prev_vendor.get("estimated_freight_total"))
        freight_delta = self._relative_delta(curr_freight, prev_freight)
        if freight_delta is not None and freight_delta >= self.material_freight_delta_threshold:
            return True, "material_freight_change"

        current_availability = self._availability_probe(current_prev_vendor)
        previous_availability = self._availability_probe(previous_prev_vendor)
        if current_availability and previous_availability and current_availability != previous_availability:
            return True, "availability_state_changed"

        current_anomaly = bool((current_prev_vendor.evidence or {}).get("anomaly_summary", {}).get("has_high_severity"))
        previous_anomaly = bool(((previous_prev_vendor.get("evidence") or {}).get("anomaly_summary") or {}).get("has_high_severity"))
        if current_anomaly and not previous_anomaly:
            return True, "high_severity_anomaly_detected"

        return False, "no_material_change"

    def apply(
        self,
        *,
        candidate_rankings: list[VendorRankingEntry],
        previous_line: dict[str, Any] | None,
    ) -> StabilityDecision:
        if not candidate_rankings:
            return StabilityDecision([], False, None, None, False, "no_candidates")

        current_top = candidate_rankings[0]
        previous_vendor_id = (previous_line or {}).get("recommended_vendor_id")
        if not previous_vendor_id:
            return StabilityDecision(candidate_rankings, False, None, None, False, "no_prior_recommendation")

        current_rank_map = {row.vendor_id: idx + 1 for idx, row in enumerate(candidate_rankings)}
        prior_rank = current_rank_map.get(previous_vendor_id)
        if prior_rank is None:
            return StabilityDecision(candidate_rankings, True, None, None, True, "prior_vendor_not_present")

        current_prior_vendor = next(row for row in candidate_rankings if row.vendor_id == previous_vendor_id)
        previous_prior_vendor = self._get_prior_vendor_state(previous_line, previous_vendor_id)
        material_change_flag, material_reason = self._material_change(current_prior_vendor, previous_prior_vendor)
        score_delta = abs((current_top.score or 0.0) - (current_prior_vendor.score or 0.0))

        if current_top.vendor_id == previous_vendor_id:
            return StabilityDecision(candidate_rankings, False, prior_rank, 0.0, material_change_flag, "prior_rank_preserved")

        if material_change_flag:
            return StabilityDecision(candidate_rankings, True, prior_rank, score_delta, True, material_reason)

        if score_delta <= self.small_delta_threshold:
            preserved = [current_prior_vendor] + [row for row in candidate_rankings if row.vendor_id != previous_vendor_id]
            for idx, row in enumerate(preserved, start=1):
                row.rank = idx
            return StabilityDecision(preserved, False, prior_rank, score_delta, False, "small_score_delta_preserved_prior_rank")

        return StabilityDecision(candidate_rankings, True, prior_rank, score_delta, False, "score_delta_exceeds_stability_threshold")


recommendation_stability_service = RecommendationStabilityService()