"""Multi-factor vendor scoring with market data integration and Phase 2A / canonical evidence awareness."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.market import FXRate, FreightRate

DEFAULT_WEIGHTS = {
    "capability_match": 0.22,
    "price_competitiveness": 0.18,
    "lead_time": 0.15,
    "reliability": 0.14,
    "logistics_fit": 0.10,
    "compliance": 0.09,
    "capacity": 0.05,
    "freshness": 0.07,
}
PHASE2A_EXTRA_WEIGHTS = {
    "evidence_completeness": 0.07,
    "evidence_confidence": 0.07,
    "freshness_adjustment": 0.06,
}
OUTCOME_EXTRA_WEIGHTS = {
    "vendor_performance": 0.05,
    "lead_time_trust": 0.04,
    "override_resilience": 0.02,
    "anomaly_resilience": 0.04,
}
LABELS = {
    "capability_match": "Capability Match",
    "price_competitiveness": "Price",
    "lead_time": "Lead Time",
    "reliability": "Reliability",
    "logistics_fit": "Logistics",
    "compliance": "Compliance",
    "capacity": "Capacity",
    "freshness": "Data Freshness",
    "evidence_completeness": "Evidence Completeness",
    "evidence_confidence": "Evidence Confidence",
    "freshness_adjustment": "Evidence Freshness",
    "vendor_performance": "Vendor Performance",
    "lead_time_trust": "Lead-time Trust",
    "override_resilience": "Override Stability",
    "anomaly_resilience": "Anomaly Resilience",
}


def load_market_context(db: Session, delivery_region: str, currency: str) -> dict:
    ctx = {"fx_rate": 1.0, "freight_per_kg": None, "data_age_days": None}
    if currency and currency != "USD":
        fx = (
            db.query(FXRate)
            .filter(FXRate.base_currency == "USD", FXRate.quote_currency == currency)
            .order_by(FXRate.effective_from.desc())
            .first()
        )
        if fx:
            ctx["fx_rate"] = float(fx.rate)
            age = (
                datetime.now(timezone.utc) - fx.effective_from.replace(tzinfo=timezone.utc)
                if fx.effective_from.tzinfo is None
                else (datetime.now(timezone.utc) - fx.effective_from)
            )
            ctx["data_age_days"] = age.days
    if delivery_region:
        fr = (
            db.query(FreightRate)
            .filter(FreightRate.destination_region.ilike(f"%{delivery_region}%"))
            .order_by(FreightRate.effective_from.desc())
            .first()
        )
        if fr and fr.rate_per_kg:
            ctx["freight_per_kg"] = float(fr.rate_per_kg)
    return ctx


def score_vendor(vendor: dict, requirements: dict, market_ctx: dict, weights: dict | None = None) -> dict:
    w = weights or DEFAULT_WEIGHTS
    phase2a = market_ctx.get("phase2a") or {}
    reference_market_price = _canonical_reference_price(phase2a) or market_ctx.get("market_median_price")

    base_breakdown: dict[str, float] = {}
    base_breakdown["capability_match"] = _cap(
        vendor.get("capabilities", []),
        requirements.get("processes", []),
        requirements.get("materials", []),
    )
    base_breakdown["price_competitiveness"] = _price(
        vendor.get("typical_unit_price"),
        reference_market_price,
    )
    base_breakdown["lead_time"] = _lt(
        vendor.get("avg_lead_time_days"),
        requirements.get("target_lead_time_days", 30),
    )
    base_breakdown["reliability"] = min(1.0, max(0.0, float(vendor.get("reliability_score", 0.5))))
    base_breakdown["logistics_fit"] = _logfit(
        vendor.get("regions_served", []),
        requirements.get("delivery_region", ""),
    )
    base_breakdown["compliance"] = _comp(
        vendor.get("certifications", []),
        requirements.get("required_certifications", []),
    )
    base_breakdown["capacity"] = _capac(
        vendor.get("capacity_profile", {}),
        requirements.get("total_quantity", 0),
    )
    age = market_ctx.get("data_age_days")
    base_breakdown["freshness"] = 1.0 if age is None else max(0.0, 1.0 - (age / 90))

    outcome_adjustment = _outcome_adjustment(market_ctx=market_ctx, vendor=vendor)
    price_damping = outcome_adjustment["price_damping_factor"]
    lead_time_damping = outcome_adjustment["lead_time_damping_factor"]
    if price_damping < 1.0:
        base_breakdown["price_competitiveness"] = round(base_breakdown["price_competitiveness"] * price_damping, 4)
    if lead_time_damping < 1.0:
        base_breakdown["lead_time"] = round(base_breakdown["lead_time"] * lead_time_damping, 4)

    total_weight = sum(w.get(k, 0.0) for k in base_breakdown)
    total_score = sum(w.get(k, 0.0) * v for k, v in base_breakdown.items())

    phase2a_breakdown = _phase2a_breakdown(phase2a=phase2a, vendor=vendor)
    if phase2a_breakdown:
        total_weight += sum(PHASE2A_EXTRA_WEIGHTS[k] for k in phase2a_breakdown)
        total_score += sum(
            PHASE2A_EXTRA_WEIGHTS[k] * phase2a_breakdown[k]
            for k in phase2a_breakdown
        )

    outcome_breakdown = outcome_adjustment["breakdown"]
    if outcome_breakdown:
        total_weight += sum(OUTCOME_EXTRA_WEIGHTS[k] for k in outcome_breakdown)
        total_score += sum(
            OUTCOME_EXTRA_WEIGHTS[k] * outcome_breakdown[k]
            for k in outcome_breakdown
        )

    normalized_total = round((total_score / total_weight), 4) if total_weight else 0.0
    normalized_total = round(max(0.0, min(1.0, normalized_total + outcome_adjustment["score_adjustment"])), 4)
    breakdown = {**base_breakdown, **phase2a_breakdown, **outcome_breakdown}

    expl_parts = []
    all_weights = {**w, **PHASE2A_EXTRA_WEIGHTS, **OUTCOME_EXTRA_WEIGHTS}
    for feature, score in sorted(
        breakdown.items(),
        key=lambda item: all_weights.get(item[0], 0.0) * item[1],
        reverse=True,
    ):
        label = LABELS.get(feature, feature)
        tag = "strong" if score >= 0.8 else "moderate" if score >= 0.5 else "weak"
        expl_parts.append(f"{label}: {tag} ({score:.0%})")

    evidence_freshness = _phase2a_status(phase2a.get("freshness_summary", {}).get("status"))
    market_freshness = "fresh" if (age is None or age < 7) else "stale" if age > 30 else "recent"

    return {
        "total_score": normalized_total,
        "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
        "weights": {
            **w,
            **({k: PHASE2A_EXTRA_WEIGHTS[k] for k in phase2a_breakdown} if phase2a_breakdown else {}),
            **({k: OUTCOME_EXTRA_WEIGHTS[k] for k in outcome_breakdown} if outcome_breakdown else {}),
        },
        "explanation": "; ".join(expl_parts),
        "explanation_json": {
            k: {
                "score": round(v, 4),
                "weight": round(all_weights.get(k, 0.0), 4),
                "contribution": round(all_weights.get(k, 0.0) * v, 4),
            }
            for k, v in breakdown.items()
        },
        "market_freshness": market_freshness,
        "evidence_freshness": evidence_freshness,
        "phase2a_used": bool(phase2a_breakdown),
        "outcome_intelligence_used": bool(outcome_breakdown or outcome_adjustment["score_adjustment"] or outcome_adjustment["confidence_adjustment"]),
        "outcome_adjustment": outcome_adjustment,
        "canonical_snapshot_used": (
            (phase2a.get("offer_evidence") or {}).get("primary_source") == "canonical_snapshot"
            or (phase2a.get("availability_evidence") or {}).get("primary_source") == "canonical_snapshot"
        ),
    }


def rank_vendors(vendors: list, requirements: dict, market_ctx: dict, weights: dict | None = None) -> list:
    scored = []
    for vendor in vendors:
        result = score_vendor(vendor, requirements, market_ctx, weights)
        result["vendor_id"] = vendor["id"]
        result["vendor_name"] = vendor.get("name", "")
        scored.append(result)
    scored.sort(key=lambda row: row["total_score"], reverse=True)
    for idx, row in enumerate(scored, start=1):
        row["rank"] = idx
    return scored


def _canonical_reference_price(phase2a: dict[str, Any]) -> float | None:
    offer_evidence = phase2a.get("offer_evidence") or {}
    if offer_evidence.get("primary_source") != "canonical_snapshot":
        return None

    selected_price_break = offer_evidence.get("selected_price_break") or {}
    for probe in (
        selected_price_break.get("unit_price"),
        offer_evidence.get("best_price"),
        (offer_evidence.get("source_metadata") or {}).get("best_price"),
    ):
        try:
            if probe not in (None, ""):
                return float(probe)
        except Exception:
            continue
    return None


def _phase2a_breakdown(phase2a: dict[str, Any], vendor: dict[str, Any]) -> dict[str, float]:
    if not phase2a:
        return {}

    confidence_summary = phase2a.get("confidence_summary") or {}
    uncertainty_flags = phase2a.get("uncertainty_flags") or {}
    freshness_summary = phase2a.get("freshness_summary") or {}
    offer_evidence = phase2a.get("offer_evidence") or {}
    availability_evidence = phase2a.get("availability_evidence") or {}
    tariff_evidence = phase2a.get("tariff_evidence") or {}
    freight_evidence = phase2a.get("freight_evidence") or {}

    critical_flags = [
        bool(uncertainty_flags.get("offer_missing")),
        bool(uncertainty_flags.get("availability_missing")),
        bool(uncertainty_flags.get("tariff_uncertain")),
        bool(uncertainty_flags.get("freight_uncertain")),
        bool(uncertainty_flags.get("hs_uncertain")),
    ]
    completeness = max(0.0, 1.0 - (sum(1 for flag in critical_flags if flag) / len(critical_flags)))

    evidence_confidence = float(confidence_summary.get("score") or 0.0)
    if offer_evidence.get("vendor_id") and offer_evidence.get("vendor_id") == vendor.get("id"):
        evidence_confidence = min(1.0, evidence_confidence + 0.08)
    elif offer_evidence.get("vendor_id") and offer_evidence.get("vendor_id") != vendor.get("id"):
        evidence_confidence = max(0.0, evidence_confidence - 0.05)

    if uncertainty_flags.get("canonical_offer_conflict"):
        evidence_confidence = max(0.0, evidence_confidence - 0.12)
    if uncertainty_flags.get("canonical_availability_conflict"):
        evidence_confidence = max(0.0, evidence_confidence - 0.10)
    if uncertainty_flags.get("canonical_offer_stale"):
        evidence_confidence = max(0.0, evidence_confidence - 0.06)
    if uncertainty_flags.get("canonical_availability_stale"):
        evidence_confidence = max(0.0, evidence_confidence - 0.05)

    freshness_adjustment = _freshness_score(freshness_summary)
    if _phase2a_status(offer_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.10)
    if _phase2a_status(tariff_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.08)
    if _phase2a_status(freight_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.08)
    if availability_evidence.get("feasible") is False:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.15)

    if offer_evidence.get("primary_source") == "canonical_snapshot" and offer_evidence.get("conflict_detected"):
        freshness_adjustment = max(0.0, freshness_adjustment - 0.10)
    if availability_evidence.get("primary_source") == "canonical_snapshot" and availability_evidence.get("has_conflict"):
        freshness_adjustment = max(0.0, freshness_adjustment - 0.10)

    return {
        "evidence_completeness": round(completeness, 4),
        "evidence_confidence": round(max(0.0, min(1.0, evidence_confidence)), 4),
        "freshness_adjustment": round(max(0.0, min(1.0, freshness_adjustment)), 4),
    }


def _phase2a_status(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value).strip().lower()


def _freshness_score(summary: dict[str, Any]) -> float:
    if not summary:
        return 0.5
    statuses = [
        _phase2a_status(summary.get("status")),
        _phase2a_status(summary.get("offer_status")),
        _phase2a_status(summary.get("availability_status")),
        _phase2a_status(summary.get("tariff_status")),
        _phase2a_status(summary.get("freight_status")),
    ]
    score = 1.0
    for status in statuses:
        if status in {"expired", "missing"}:
            score -= 0.22
        elif status in {"stale", "mixed"}:
            score -= 0.16
        elif status in {"uncertain", "unknown"}:
            score -= 0.12
        elif status == "recent":
            score -= 0.05
    return max(0.0, min(1.0, score))


def _cap(caps, procs, mats):
    if not procs and not mats:
        return 0.5
    total = len(procs) + len(mats)
    matches = 0
    cap_processes = {c.get("process", "").lower() for c in caps}
    cap_materials = {c.get("material_family", "").lower() for c in caps}
    for process in procs:
        if process.lower() in cap_processes:
            matches += 1
    for material in mats:
        if any(material.lower() in candidate for candidate in cap_materials):
            matches += 1
    return min(1.0, matches / max(total, 1))


def _price(vendor_price, market_median):
    if not vendor_price or not market_median:
        return 0.5
    ratio = float(vendor_price) / float(market_median)
    if ratio <= 0.8:
        return 1.0
    if ratio >= 1.5:
        return 0.0
    return max(0.0, 1.0 - (ratio - 0.8) / 0.7)


def _lt(vendor_lead_time, target_lead_time_days):
    if not vendor_lead_time or not target_lead_time_days:
        return 0.5
    ratio = float(vendor_lead_time) / float(target_lead_time_days)
    if ratio <= 0.7:
        return 1.0
    if ratio >= 2.0:
        return 0.0
    return max(0.0, 1.0 - (ratio - 0.7) / 1.3)


def _logfit(regions_served, delivery_region):
    if not delivery_region:
        return 0.7
    if not regions_served:
        return 0.5
    delivery = str(delivery_region).lower()
    normalized = [str(r).lower() for r in regions_served]
    if any(delivery in region or region in delivery for region in normalized):
        return 1.0
    return 0.35


def _comp(vendor_certs, required_certs):
    if not required_certs:
        return 0.7
    if not vendor_certs:
        return 0.0
    have = {str(cert).lower() for cert in vendor_certs}
    required = [str(cert).lower() for cert in required_certs]
    matched = sum(1 for cert in required if cert in have)
    return min(1.0, matched / max(len(required), 1))


def _capac(profile, total_quantity):
    if not profile or not total_quantity:
        return 0.5
    monthly_capacity = float(profile.get("monthly_capacity", 0) or 0)
    if monthly_capacity <= 0:
        return 0.4
    ratio = float(total_quantity) / monthly_capacity
    if ratio <= 0.25:
        return 1.0
    if ratio >= 1.0:
        return 0.2
    return max(0.2, 1.0 - ((ratio - 0.25) / 0.75) * 0.8)

def _outcome_adjustment(market_ctx: dict[str, Any], vendor: dict[str, Any]) -> dict[str, Any]:
    intelligence_map = market_ctx.get("outcome_intelligence_by_vendor") or {}
    adjustment = intelligence_map.get(vendor.get("id")) or {}
    performance = adjustment.get("performance_adjustment") or {}
    override_meta = adjustment.get("override_adjustment") or {}
    anomaly = adjustment.get("anomaly_adjustment") or {}

    breakdown: dict[str, float] = {}
    if performance.get("available") and performance.get("sample_size", 0) >= 2:
        on_time = float(performance.get("on_time_rate") or 0.0)
        win_rate = float(performance.get("po_win_rate") or 0.0)
        issue_rate = float(performance.get("issue_rate") or 0.0)
        vendor_performance = 0.5 + ((on_time - 0.5) * 0.35) + ((win_rate - 0.5) * 0.15) - (issue_rate * 0.20)
        breakdown["vendor_performance"] = round(max(0.0, min(1.0, vendor_performance)), 4)

        lead_var = float(performance.get("lead_time_variance") or 0.0)
        lead_trust = 0.5 + ((on_time - 0.5) * 0.45) - min(0.20, lead_var / 100.0)
        breakdown["lead_time_trust"] = round(max(0.0, min(1.0, lead_trust)), 4)

    if override_meta.get("sample_size", 0) >= 3:
        rate = float(override_meta.get("override_rate") or 0.0)
        breakdown["override_resilience"] = round(max(0.0, min(1.0, 1.0 - rate)), 4)

    anomaly_total = int(anomaly.get("price_count") or 0) + int(anomaly.get("lead_time_count") or 0) + int(anomaly.get("availability_count") or 0)
    if anomaly_total:
        breakdown["anomaly_resilience"] = round(max(0.0, min(1.0, 1.0 - min(0.6, anomaly_total * 0.15))), 4)

    return {
        "breakdown": breakdown,
        "score_adjustment": float(adjustment.get("score_adjustment") or 0.0),
        "confidence_adjustment": float(adjustment.get("confidence_adjustment") or 0.0),
        "price_damping_factor": max(0.75, 1.0 - float(anomaly.get("price_penalty") or 0.0)),
        "lead_time_damping_factor": max(0.70, 1.0 - float(anomaly.get("lead_time_penalty") or 0.0)),
        "explanation_fragments": list(adjustment.get("explanation_fragments") or []),
    }

# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 additions — strategy weight profiles, new scoring dimensions,
# bounded influence, trust-tier multiplier, confidence classifier.
# ═════════════════════════════════════════════════════════════════════════════

WEIGHTS_FASTEST_LOCAL = {
    "capability_match": 0.15,
    "price_competitiveness": 0.10,
    "lead_time": 0.35,
    "reliability": 0.15,
    "logistics_fit": 0.15,
    "compliance": 0.05,
    "capacity": 0.05,
}

WEIGHTS_BEST_DOMESTIC_VALUE = {
    "capability_match": 0.22,
    "price_competitiveness": 0.22,
    "lead_time": 0.18,
    "reliability": 0.15,
    "logistics_fit": 0.10,
    "compliance": 0.08,
    "capacity": 0.05,
}

WEIGHTS_LOWEST_LANDED_COST = {
    "capability_match": 0.18,
    "price_competitiveness": 0.10,
    "landed_cost": 0.28,
    "lead_time": 0.12,
    "reliability": 0.12,
    "logistics_fit": 0.08,
    "tariff_impact": 0.07,
    "compliance": 0.05,
}

STRATEGY_WEIGHT_PROFILES = {
    "fastest_local": WEIGHTS_FASTEST_LOCAL,
    "best_domestic_value": WEIGHTS_BEST_DOMESTIC_VALUE,
    "lowest_landed_cost": WEIGHTS_LOWEST_LANDED_COST,
    "balanced": DEFAULT_WEIGHTS,
}

TRUST_TIER_SCORE_MULTIPLIER = {
    "PLATINUM":   1.00,
    "GOLD":       0.97,
    "SILVER":     0.90,
    "BRONZE":     0.78,
    "UNVERIFIED": 0.60,
}

# Bounded-influence cap: no single dimension may contribute more than this
# fraction of the final total.  Implements §5 "bounded influence so no single
# signal (like price) completely dominates."
BOUNDED_INFLUENCE_CAP = 0.30


def _landed_cost_score(landed_cost, market_median_landed):
    """Score based on total landed cost vs market median landed cost."""
    if landed_cost is None or market_median_landed is None:
        return 0.5
    try:
        lc = float(landed_cost)
        med = float(market_median_landed)
    except Exception:
        return 0.5
    if med <= 0:
        return 0.5
    ratio = lc / med
    if ratio <= 0.8:
        return 1.0
    if ratio >= 1.5:
        return 0.0
    return max(0.0, 1.0 - (ratio - 0.8) / 0.7)


def _tariff_impact_score(tariff_rate):
    """Lower score for higher tariff rate. Zero tariff = 1.0."""
    if tariff_rate is None:
        return 0.7  # unknown tariff — neutral
    try:
        rate = float(tariff_rate)
    except Exception:
        return 0.7
    if rate <= 0:
        return 1.0
    if rate >= 0.20:
        return 0.30
    if rate >= 0.10:
        return 0.60
    if rate >= 0.05:
        return 0.80
    return max(0.3, 1.0 - (rate / 0.05) * 0.2)


def _spec_match_score(vendor: dict, requirements: dict) -> float:
    """Score based on tolerance/standard/spec closeness."""
    required_tolerance = (requirements.get("tolerance_class") or "").lower()
    required_standards = [str(s).lower() for s in (requirements.get("standards") or [])]
    if not required_tolerance and not required_standards:
        return 0.7
    caps = vendor.get("capabilities", []) or []
    vendor_certs = {str(c).lower() for c in (vendor.get("certifications") or [])}
    # Standards hit
    std_hits = sum(1 for s in required_standards if s in vendor_certs)
    std_score = (std_hits / len(required_standards)) if required_standards else 1.0
    # Tolerance: any capability reporting tolerance_class match?
    tol_score = 0.7
    if required_tolerance:
        vendor_tols = {
            str((c.get("source_metadata") or {}).get("tolerance_class") or "").lower()
            for c in caps
        }
        tol_score = 1.0 if required_tolerance in vendor_tols else 0.4
    return round(0.5 * std_score + 0.5 * tol_score, 4)


def _geo_strategy_score(vendor: dict, strategy: str, geo_buckets: dict | None) -> float:
    """Bonus/penalty based on which geo tier the vendor is in, per strategy."""
    tier = vendor.get("geo_tier") or ""
    if strategy == "fastest_local":
        if tier == "local":
            return 1.0
        if tier == "regional":
            return 0.85
        if tier == "national":
            return 0.55
        return 0.20  # global penalised
    if strategy == "best_domestic_value":
        if tier in ("local", "regional"):
            return 0.90
        if tier == "national":
            return 0.80
        return 0.55
    if strategy == "lowest_landed_cost":
        # Landed cost dimension already rewards the cheap path; geo is neutral.
        return 0.7
    return 0.7


def _apply_bounded_influence(
    breakdown: dict,
    weights: dict,
    cap: float = BOUNDED_INFLUENCE_CAP,
) -> tuple[float, dict]:
    """
    Compute total_score with a per-dimension contribution cap.

    Returns (total_score, capped_contributions).
    """
    contributions = {k: float(weights.get(k, 0.0)) * float(v) for k, v in breakdown.items()}
    total = sum(contributions.values())
    if total <= 0:
        return 0.0, contributions
    capped = {}
    for k, contrib in contributions.items():
        fraction = contrib / total
        if fraction > cap:
            capped[k] = total * cap
        else:
            capped[k] = contrib
    return sum(capped.values()), capped


def score_vendor_with_strategy(
    vendor: dict,
    requirements: dict,
    market_ctx: dict,
    strategy: str = "balanced",
    geo_buckets: dict | None = None,
) -> dict:
    """
    Strategy-aware vendor scorer (Phase 3).

    Adds on top of score_vendor():
      - strategy-specific weight profile
      - landed_cost_score, tariff_impact_score, geo_strategy_score dimensions
      - trust_tier multiplier
      - anomaly penalty from market_ctx["anomaly_flags"]
      - bounded-influence cap (no single dimension > 30 % of total)
      - returns confidence label + score.

    Backward-compat: score_vendor() remains unchanged.
    """
    weights = STRATEGY_WEIGHT_PROFILES.get(strategy, DEFAULT_WEIGHTS)
    base_result = score_vendor(vendor=vendor, requirements=requirements, market_ctx=market_ctx, weights=weights)

    breakdown = dict(base_result.get("breakdown", {}))
    effective_weights = dict(base_result.get("weights", weights))

    # New dimensions
    if "landed_cost" in weights:
        breakdown["landed_cost"] = _landed_cost_score(
            market_ctx.get("landed_cost_total"),
            market_ctx.get("market_median_landed"),
        )
        effective_weights["landed_cost"] = weights["landed_cost"]
    if "tariff_impact" in weights:
        breakdown["tariff_impact"] = _tariff_impact_score(market_ctx.get("tariff_rate"))
        effective_weights["tariff_impact"] = weights["tariff_impact"]

    # Geo strategy bonus (always applied, small weight)
    geo_bonus_weight = 0.05
    breakdown["geo_strategy"] = _geo_strategy_score(vendor, strategy, geo_buckets)
    effective_weights["geo_strategy"] = geo_bonus_weight

    # Spec match (additive small weight when requirements carry specs)
    if requirements.get("tolerance_class") or requirements.get("standards"):
        breakdown["spec_match"] = _spec_match_score(vendor, requirements)
        effective_weights["spec_match"] = 0.05

    # Bounded-influence-aware total
    total_weight = sum(effective_weights.values()) or 1.0
    weighted_total = sum(
        float(effective_weights.get(k, 0.0)) * float(v) for k, v in breakdown.items()
    )
    normalized = weighted_total / total_weight
    capped_total, capped_contribs = _apply_bounded_influence(
        breakdown=breakdown, weights=effective_weights, cap=BOUNDED_INFLUENCE_CAP,
    )
    # Use capped contributions, re-normalised
    capped_normalized = (capped_total / total_weight) if total_weight else 0.0
    # Blend 80 % capped / 20 % raw so multiple strong signals are still rewarded
    total_score = max(0.0, min(1.0, 0.8 * capped_normalized + 0.2 * normalized))

    # Trust-tier multiplier
    tier = (vendor.get("trust_tier") or "UNVERIFIED").upper()
    multiplier = TRUST_TIER_SCORE_MULTIPLIER.get(tier, 0.60)
    total_score = max(0.0, min(1.0, total_score * multiplier))

    # Anomaly penalty
    anomaly_flags = market_ctx.get("anomaly_flags") or []
    severity_penalty = {
        "LOW": -0.02, "MEDIUM": -0.08, "HIGH": -0.15, "CRITICAL": -0.30,
    }
    for flag in anomaly_flags:
        sev = flag.get("severity") if isinstance(flag, dict) else getattr(flag, "severity", None)
        total_score += severity_penalty.get(sev, 0.0)
    total_score = max(0.0, min(1.0, total_score))

    # Confidence
    evidence_count = int((market_ctx.get("evidence_context") or {}).get("evidence_count") or 0)
    confidence_label, confidence_score = classify_confidence(
        score=total_score,
        vendor=vendor,
        evidence={"evidence_count": evidence_count},
    )

    return {
        **base_result,
        "strategy": strategy,
        "total_score": round(total_score, 4),
        "breakdown": {k: round(float(v), 4) for k, v in breakdown.items()},
        "weights": effective_weights,
        "trust_tier": tier,
        "trust_tier_multiplier": multiplier,
        "anomaly_penalty_applied": sum(
            severity_penalty.get(
                f.get("severity") if isinstance(f, dict) else getattr(f, "severity", None), 0.0,
            )
            for f in anomaly_flags
        ),
        "bounded_influence_cap": BOUNDED_INFLUENCE_CAP,
        "confidence": confidence_label,
        "confidence_score": confidence_score,
        "explanation": base_result.get("explanation", "") + f" | strategy={strategy} tier={tier}",
    }


def classify_confidence(score: float, vendor: dict, evidence: dict | None = None) -> tuple[str, float]:
    """
    Return (confidence_label, confidence_score).

    HIGH:   score ≥ 0.75 AND trust_tier ∈ {PLATINUM, GOLD} AND evidence ≥ 3
    MEDIUM: score ≥ 0.55 AND trust_tier ∈ {GOLD, SILVER} AND evidence ≥ 1
    LOW:    everything else, esp. UNVERIFIED tier
    """
    evidence = evidence or {}
    ev_count = int(evidence.get("evidence_count", 0) or 0)
    tier = (vendor.get("trust_tier") or "UNVERIFIED").upper()
    s = float(score)

    if tier == "UNVERIFIED":
        return "LOW", round(max(0.2, s - 0.10), 4)

    if s >= 0.75 and tier in ("PLATINUM", "GOLD") and ev_count >= 3:
        return "HIGH", round(min(1.0, s + 0.05), 4)

    if s >= 0.55 and tier in ("GOLD", "SILVER") and ev_count >= 1:
        return "MEDIUM", round(s, 4)

    return "LOW", round(max(0.2, s - 0.10), 4)


def rank_vendors_by_strategy(
    vendors: list,
    requirements: dict,
    market_ctx: dict,
    strategy: str = "balanced",
    geo_buckets: dict | None = None,
) -> list:
    """Rank vendors using the strategy-aware scorer. Preserves rank_vendors()."""
    scored = []
    for vendor in vendors:
        # Merge per-vendor market context (landed_cost, tariff_rate, anomaly_flags, evidence)
        per_vendor_ctx = dict(market_ctx)
        per_vendor_ctx.update(vendor.get("per_vendor_market_ctx") or {})
        result = score_vendor_with_strategy(
            vendor=vendor,
            requirements=requirements,
            market_ctx=per_vendor_ctx,
            strategy=strategy,
            geo_buckets=geo_buckets,
        )
        result["vendor_id"] = vendor.get("id")
        result["vendor_name"] = vendor.get("name", "")
        scored.append(result)
    scored.sort(key=lambda row: row["total_score"], reverse=True)
    for idx, row in enumerate(scored, start=1):
        row["rank"] = idx
    return scored
