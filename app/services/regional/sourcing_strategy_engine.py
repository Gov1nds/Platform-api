"""
Sourcing Strategy Engine.

Implements Execution Plan §3 + §6: produce three named strategy outputs
from a set of scored vendors and a GeoBuckets split.

  - Strategy 1: Fastest Local Option
  - Strategy 2: Best Domestic Value
  - Strategy 3: Lowest Landed Cost (Global)

Each strategy yields top_option + runner_ups with a narrative explaining
trade-offs (e.g. "Vendor C saves 30% on unit cost but adds ₹5000 freight
and 5% import duty"). Landed-cost math uses Decimal throughout.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.regional.geo_tier_service import (
    GeoBuckets, GeoContext, LogisticsProfile,
)

logger = logging.getLogger(__name__)


STRATEGY_FASTEST_LOCAL = "fastest_local"
STRATEGY_BEST_DOMESTIC = "best_domestic_value"
STRATEGY_LOWEST_LANDED = "lowest_landed_cost"

STRATEGY_LABELS = {
    STRATEGY_FASTEST_LOCAL: "Fastest Local Option",
    STRATEGY_BEST_DOMESTIC: "Best Domestic Value",
    STRATEGY_LOWEST_LANDED: "Lowest Landed Cost (Global)",
}


@dataclass
class LandedCostBreakdown:
    unit_price: Decimal
    quantity: Decimal
    freight_cost: Decimal
    tariff_amount: Decimal
    fx_conversion_cost: Decimal
    total_landed_cost: Decimal
    landed_cost_per_unit: Decimal
    currency: str
    breakdown_narrative: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_price": str(self.unit_price),
            "quantity": str(self.quantity),
            "freight_cost": str(self.freight_cost),
            "tariff_amount": str(self.tariff_amount),
            "fx_conversion_cost": str(self.fx_conversion_cost),
            "total_landed_cost": str(self.total_landed_cost),
            "landed_cost_per_unit": str(self.landed_cost_per_unit),
            "currency": self.currency,
            "breakdown_narrative": self.breakdown_narrative,
        }


@dataclass
class StrategyOption:
    vendor_id: str
    vendor_name: str
    vendor_country: str | None
    vendor_state_region: str | None
    geo_tier: str
    rank_within_strategy: int
    strategy_score: float
    confidence: str
    confidence_score: float
    unit_price: Decimal | None
    freight_cost: Decimal | None
    tariff_amount: Decimal | None
    fx_conversion_cost: Decimal | None
    landed_cost_total: Decimal | None
    landed_cost_per_unit: Decimal | None
    currency: str
    lead_time_min_days: int | None
    lead_time_max_days: int | None
    lead_time_typical_days: float | None
    lead_time_reliability_score: float | None
    award_ready: bool
    rfq_recommended: bool
    rationale_narrative: str
    trade_off_narrative: str
    risk_flags: list[str] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    evidence_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def _d(x):
            return str(x) if x is not None else None
        return {
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "vendor_country": self.vendor_country,
            "vendor_state_region": self.vendor_state_region,
            "geo_tier": self.geo_tier,
            "rank_within_strategy": self.rank_within_strategy,
            "strategy_score": round(self.strategy_score, 4),
            "confidence": self.confidence,
            "confidence_score": round(self.confidence_score, 4),
            "unit_price": _d(self.unit_price),
            "freight_cost": _d(self.freight_cost),
            "tariff_amount": _d(self.tariff_amount),
            "fx_conversion_cost": _d(self.fx_conversion_cost),
            "landed_cost_total": _d(self.landed_cost_total),
            "landed_cost_per_unit": _d(self.landed_cost_per_unit),
            "currency": self.currency,
            "lead_time_min_days": self.lead_time_min_days,
            "lead_time_max_days": self.lead_time_max_days,
            "lead_time_typical_days": self.lead_time_typical_days,
            "lead_time_reliability_score": self.lead_time_reliability_score,
            "award_ready": bool(self.award_ready),
            "rfq_recommended": bool(self.rfq_recommended),
            "rationale_narrative": self.rationale_narrative,
            "trade_off_narrative": self.trade_off_narrative,
            "risk_flags": list(self.risk_flags),
            "score_breakdown": dict(self.score_breakdown),
            "evidence_summary": dict(self.evidence_summary),
        }


@dataclass
class SourcingStrategy:
    strategy_name: str
    strategy_label: str
    top_option: StrategyOption | None
    runner_up_options: list[StrategyOption]
    strategy_confidence: str
    rfq_required: bool
    strategy_narrative: str
    geo_tier_context: dict[str, Any]
    commodity_signal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "strategy_label": self.strategy_label,
            "top_option": self.top_option.to_dict() if self.top_option else None,
            "runner_up_options": [r.to_dict() for r in self.runner_up_options],
            "strategy_confidence": self.strategy_confidence,
            "rfq_required": bool(self.rfq_required),
            "strategy_narrative": self.strategy_narrative,
            "geo_tier_context": dict(self.geo_tier_context),
            "commodity_signal": self.commodity_signal,
        }


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


class SourcingStrategyEngine:
    """Produce three named sourcing strategies from scored vendor data."""

    # ── Landed cost ──────────────────────────────────────────────────────

    def compute_landed_cost(
        self,
        vendor: dict[str, Any],
        unit_price: Decimal | float | int | None,
        quantity: Decimal | float | int,
        logistics_profile: LogisticsProfile | None,
        tariff_rate: float | Decimal | None,
        fx_rate: float | Decimal | None,
        currency: str = "USD",
    ) -> LandedCostBreakdown:
        up = _as_decimal(unit_price, "0")
        qty = _as_decimal(quantity, "0") or Decimal("1")
        merchandise = up * qty

        freight = Decimal("0")
        if logistics_profile and logistics_profile.est_freight_cost_usd is not None:
            freight = _as_decimal(logistics_profile.est_freight_cost_usd, "0")

        tariff = Decimal("0")
        if tariff_rate:
            tariff = (merchandise * _as_decimal(tariff_rate)).quantize(Decimal("0.01"))

        fx_cost = Decimal("0")
        if fx_rate is not None and currency != "USD":
            fx_dec = _as_decimal(fx_rate, "1")
            if fx_dec > 0:
                # FX conversion cost: assume 1.5% spread cost relative to merchandise
                fx_cost = (merchandise * Decimal("0.015")).quantize(Decimal("0.01"))

        total = (merchandise + freight + tariff + fx_cost).quantize(Decimal("0.01"))
        per_unit = (total / qty).quantize(Decimal("0.0001")) if qty > 0 else total

        narrative = (
            f"Unit {up} × Qty {qty} = {merchandise}; Freight {freight}; "
            f"Tariff {tariff}; FX spread {fx_cost}; Total landed {total} {currency}."
        )
        return LandedCostBreakdown(
            unit_price=up,
            quantity=qty,
            freight_cost=freight,
            tariff_amount=tariff,
            fx_conversion_cost=fx_cost,
            total_landed_cost=total,
            landed_cost_per_unit=per_unit,
            currency=currency,
            breakdown_narrative=narrative,
        )

    # ── Confidence ───────────────────────────────────────────────────────

    def _classify_confidence(
        self,
        score: float,
        trust_tier: str,
        evidence_count: int,
    ) -> tuple[str, float]:
        if (
            score >= 0.75
            and trust_tier in ("PLATINUM", "GOLD")
            and evidence_count >= 3
        ):
            return "HIGH", round(min(1.0, score + 0.05), 4)
        if (
            score >= 0.55
            and trust_tier in ("GOLD", "SILVER")
            and evidence_count >= 1
        ):
            return "MEDIUM", round(score, 4)
        return "LOW", round(max(0.2, score - 0.10), 4)

    # ── Narratives ───────────────────────────────────────────────────────

    def build_trade_off_narrative(
        self,
        strategy_name: str,
        vendor: dict[str, Any],
        landed_cost: LandedCostBreakdown | None,
        comparisons: dict[str, Any] | None = None,
    ) -> str:
        name = vendor.get("name", "Vendor")
        country = vendor.get("country") or ""
        tier = vendor.get("geo_tier") or "?"
        comps = comparisons or {}

        if strategy_name == STRATEGY_FASTEST_LOCAL:
            lt = vendor.get("avg_lead_time_days") or "?"
            return (
                f"{name} ({country}, {tier}): prioritises speed — "
                f"~{lt} days lead time, minimal freight, higher unit price acceptable."
            )
        if strategy_name == STRATEGY_BEST_DOMESTIC:
            lt = vendor.get("avg_lead_time_days") or "?"
            return (
                f"{name} ({country}, {tier}): balanced capability match and price, "
                f"moderate lead time (~{lt} days) and domestic freight."
            )
        if strategy_name == STRATEGY_LOWEST_LANDED and landed_cost is not None:
            savings_vs = comps.get("savings_vs_domestic_pct")
            if savings_vs is not None:
                return (
                    f"{name} ({country}): lowest unit price; saves {savings_vs:.1f}% "
                    f"vs domestic but adds {landed_cost.freight_cost} freight "
                    f"and {landed_cost.tariff_amount} tariff — long lead time."
                )
            return (
                f"{name} ({country}): lowest landed cost {landed_cost.total_landed_cost} "
                f"{landed_cost.currency}; trade-off is long transit and duty."
            )
        return f"{name} ({country}, {tier})."

    # ── Option building ──────────────────────────────────────────────────

    def _vendor_to_option(
        self,
        vendor: dict[str, Any],
        rank: int,
        strategy_name: str,
        strategy_score: float,
        landed_cost: LandedCostBreakdown | None,
        logistics_profile: LogisticsProfile | None,
        currency: str,
        evidence: dict[str, Any],
        comparisons: dict[str, Any] | None = None,
    ) -> StrategyOption:
        tier = (vendor.get("trust_tier") or "UNVERIFIED").upper()
        evidence_count = int(evidence.get("evidence_count", 0) or 0)

        conf_label, conf_score = self._classify_confidence(strategy_score, tier, evidence_count)

        award_ready = bool(evidence.get("award_ready", False))
        rfq_rec = bool(evidence.get("rfq_recommended", not award_ready))

        risk_flags: list[str] = list(evidence.get("risk_flags") or [])
        if tier == "UNVERIFIED":
            risk_flags.append("unverified_vendor_tier")
        if evidence.get("no_performance_history"):
            risk_flags.append("no_performance_history")
        if conf_label == "LOW":
            risk_flags.append("low_confidence_recommendation")

        trade_off = self.build_trade_off_narrative(strategy_name, vendor, landed_cost, comparisons)
        rationale = (
            f"Selected under '{STRATEGY_LABELS.get(strategy_name, strategy_name)}' strategy "
            f"with score {strategy_score:.3f} and trust tier {tier}. "
            f"Evidence count: {evidence_count}."
        )

        return StrategyOption(
            vendor_id=vendor["id"],
            vendor_name=vendor.get("name", ""),
            vendor_country=vendor.get("country"),
            vendor_state_region=vendor.get("region"),
            geo_tier=vendor.get("geo_tier") or "?",
            rank_within_strategy=rank,
            strategy_score=strategy_score,
            confidence=conf_label,
            confidence_score=conf_score,
            unit_price=landed_cost.unit_price if landed_cost else None,
            freight_cost=landed_cost.freight_cost if landed_cost else None,
            tariff_amount=landed_cost.tariff_amount if landed_cost else None,
            fx_conversion_cost=landed_cost.fx_conversion_cost if landed_cost else None,
            landed_cost_total=landed_cost.total_landed_cost if landed_cost else None,
            landed_cost_per_unit=landed_cost.landed_cost_per_unit if landed_cost else None,
            currency=currency,
            lead_time_min_days=evidence.get("lead_time_min_days"),
            lead_time_max_days=evidence.get("lead_time_max_days"),
            lead_time_typical_days=evidence.get("lead_time_typical_days"),
            lead_time_reliability_score=evidence.get("lead_time_reliability_score"),
            award_ready=award_ready,
            rfq_recommended=rfq_rec,
            rationale_narrative=rationale,
            trade_off_narrative=trade_off,
            risk_flags=risk_flags,
            score_breakdown=dict(evidence.get("score_breakdown") or {}),
            evidence_summary={
                k: v for k, v in evidence.items()
                if k not in ("score_breakdown",)
            },
        )

    # ── Strategy generation ──────────────────────────────────────────────

    def generate_sourcing_strategies(
        self,
        scored_vendors: list[dict[str, Any]],
        geo_buckets: GeoBuckets,
        geo_ctx: GeoContext,
        requirements: dict[str, Any],
        db: Session,
        currency: str = "USD",
    ) -> list[SourcingStrategy]:
        """
        scored_vendors is a list of dicts; each dict MUST contain:
          id, name, country, region, geo_tier, trust_tier,
          strategy_scores: {fastest_local, best_domestic_value, lowest_landed_cost},
          landed_cost: LandedCostBreakdown | None,
          logistics_profile: LogisticsProfile | None,
          evidence: {...} (award_ready, rfq_recommended, score_breakdown, etc.)
        """
        strategies: list[SourcingStrategy] = []
        counts = geo_buckets.counts()

        # Index vendors by id for membership checks
        by_id = {v["id"]: v for v in scored_vendors}
        local_ids = {v["id"] for v in geo_buckets.local}
        regional_ids = {v["id"] for v in geo_buckets.regional}
        national_ids = {v["id"] for v in geo_buckets.national}
        global_ids = {v["id"] for v in geo_buckets.global_}

        # ── Strategy 1: Fastest Local ─────────────────────────────────────
        local_pool = [v for v in scored_vendors if v["id"] in local_ids]
        if not local_pool:
            # Escalate to regional
            local_pool = [v for v in scored_vendors if v["id"] in regional_ids]
            escalated_local = "escalated_to_regional"
        else:
            escalated_local = None
        strategies.append(
            self._build_strategy(
                strategy_name=STRATEGY_FASTEST_LOCAL,
                eligible=local_pool,
                score_key=STRATEGY_FASTEST_LOCAL,
                currency=currency,
                counts=counts,
                extra_narrative=(
                    f"No local vendors found — {escalated_local}"
                    if escalated_local else
                    f"{len(local_pool)} local vendors considered in requester's state/region."
                ),
            )
        )

        # ── Strategy 2: Best Domestic Value ───────────────────────────────
        domestic_ids = local_ids | regional_ids | national_ids
        domestic_pool = [v for v in scored_vendors if v["id"] in domestic_ids]
        strategies.append(
            self._build_strategy(
                strategy_name=STRATEGY_BEST_DOMESTIC,
                eligible=domestic_pool,
                score_key=STRATEGY_BEST_DOMESTIC,
                currency=currency,
                counts=counts,
                extra_narrative=(
                    f"{len(domestic_pool)} domestic vendors considered "
                    f"(local + neighbouring states + national)."
                ),
            )
        )

        # ── Strategy 3: Lowest Landed Cost (Global) ───────────────────────
        landed_pool = [v for v in scored_vendors if v.get("landed_cost") is not None]
        if not landed_pool:
            landed_pool = list(scored_vendors)
        strategies.append(
            self._build_strategy(
                strategy_name=STRATEGY_LOWEST_LANDED,
                eligible=landed_pool,
                score_key=STRATEGY_LOWEST_LANDED,
                currency=currency,
                counts=counts,
                extra_narrative=(
                    f"{len(landed_pool)} vendors considered for landed-cost "
                    f"evaluation (including {len(global_ids)} global candidates)."
                ),
                sort_by_landed_cost=True,
            )
        )
        return strategies

    def _build_strategy(
        self,
        strategy_name: str,
        eligible: list[dict[str, Any]],
        score_key: str,
        currency: str,
        counts: dict[str, int],
        extra_narrative: str,
        sort_by_landed_cost: bool = False,
    ) -> SourcingStrategy:
        if not eligible:
            return SourcingStrategy(
                strategy_name=strategy_name,
                strategy_label=STRATEGY_LABELS[strategy_name],
                top_option=None,
                runner_up_options=[],
                strategy_confidence="LOW",
                rfq_required=True,
                strategy_narrative=(
                    f"No eligible vendors for '{STRATEGY_LABELS[strategy_name]}'. "
                    f"Issue an RFQ to discover candidates."
                ),
                geo_tier_context=dict(counts),
            )

        # Rank eligible list
        if sort_by_landed_cost:
            def landed_key(v: dict[str, Any]) -> Decimal:
                lc = v.get("landed_cost")
                if lc is None:
                    return Decimal("999999999")
                return _as_decimal(lc.total_landed_cost if hasattr(lc, "total_landed_cost") else lc.get("total_landed_cost"))
            ranked = sorted(eligible, key=landed_key)
        else:
            def score_key_fn(v: dict[str, Any]) -> float:
                scores = v.get("strategy_scores") or {}
                return float(scores.get(score_key, 0.0))
            ranked = sorted(eligible, key=score_key_fn, reverse=True)

        options: list[StrategyOption] = []
        top_landed: Decimal | None = None
        top_domestic_landed: Decimal | None = None

        # Precompute best domestic landed cost for comparisons narrative
        if sort_by_landed_cost:
            domestic_candidates = [
                v for v in eligible if v.get("geo_tier") != "global" and v.get("landed_cost")
            ]
            if domestic_candidates:
                best_dom = min(
                    domestic_candidates,
                    key=lambda v: _as_decimal(
                        getattr(v.get("landed_cost"), "total_landed_cost", None)
                        if hasattr(v.get("landed_cost"), "total_landed_cost")
                        else (v.get("landed_cost") or {}).get("total_landed_cost")
                    ),
                )
                lc = best_dom.get("landed_cost")
                top_domestic_landed = (
                    _as_decimal(lc.total_landed_cost)
                    if hasattr(lc, "total_landed_cost")
                    else _as_decimal((lc or {}).get("total_landed_cost"))
                )

        for idx, vendor in enumerate(ranked[:3], start=1):
            strategy_score = float(
                (vendor.get("strategy_scores") or {}).get(score_key, 0.0)
            )
            comparisons: dict[str, Any] = {}
            landed = vendor.get("landed_cost")
            if sort_by_landed_cost and landed is not None and top_domestic_landed is not None:
                lc_total = _as_decimal(
                    landed.total_landed_cost if hasattr(landed, "total_landed_cost")
                    else (landed or {}).get("total_landed_cost")
                )
                if top_domestic_landed > 0 and lc_total > 0:
                    savings_pct = float(
                        ((top_domestic_landed - lc_total) / top_domestic_landed) * Decimal("100")
                    )
                    comparisons["savings_vs_domestic_pct"] = round(savings_pct, 2)

            option = self._vendor_to_option(
                vendor=vendor,
                rank=idx,
                strategy_name=strategy_name,
                strategy_score=strategy_score,
                landed_cost=landed,
                logistics_profile=vendor.get("logistics_profile"),
                currency=currency,
                evidence=dict(vendor.get("evidence") or {}),
                comparisons=comparisons,
            )
            options.append(option)

        top = options[0] if options else None
        runners = options[1:] if len(options) > 1 else []

        # Strategy-level confidence and RFQ gating
        if top is None:
            strategy_conf = "LOW"
            rfq_required = True
        else:
            strategy_conf = top.confidence
            rfq_required = bool(top.rfq_recommended) or strategy_conf == "LOW"

        narrative = (
            f"{STRATEGY_LABELS[strategy_name]}: {extra_narrative} "
            + (f"Top: {top.vendor_name} — {top.trade_off_narrative}" if top else "")
        )
        return SourcingStrategy(
            strategy_name=strategy_name,
            strategy_label=STRATEGY_LABELS[strategy_name],
            top_option=top,
            runner_up_options=runners,
            strategy_confidence=strategy_conf,
            rfq_required=rfq_required,
            strategy_narrative=narrative,
            geo_tier_context=dict(counts),
        )


sourcing_strategy_engine = SourcingStrategyEngine()
