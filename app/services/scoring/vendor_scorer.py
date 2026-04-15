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