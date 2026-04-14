
"""Multi-factor vendor scoring with market data integration and Phase 2A evidence awareness."""
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
    base_breakdown: dict[str, float] = {}
    base_breakdown["capability_match"] = _cap(
        vendor.get("capabilities", []),
        requirements.get("processes", []),
        requirements.get("materials", []),
    )
    base_breakdown["price_competitiveness"] = _price(
        vendor.get("typical_unit_price"),
        market_ctx.get("market_median_price"),
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

    total_weight = sum(w.get(k, 0.0) for k in base_breakdown)
    total_score = sum(w.get(k, 0.0) * v for k, v in base_breakdown.items())

    phase2a = market_ctx.get("phase2a") or {}
    phase2a_breakdown = _phase2a_breakdown(phase2a=phase2a, vendor=vendor)
    if phase2a_breakdown:
        total_weight += sum(PHASE2A_EXTRA_WEIGHTS.values())
        total_score += sum(
            PHASE2A_EXTRA_WEIGHTS[k] * phase2a_breakdown[k]
            for k in phase2a_breakdown
        )

    normalized_total = round((total_score / total_weight), 4) if total_weight else 0.0
    breakdown = {**base_breakdown, **phase2a_breakdown}

    expl_parts = []
    all_weights = {**w, **PHASE2A_EXTRA_WEIGHTS}
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
        "weights": {**w, **({k: PHASE2A_EXTRA_WEIGHTS[k] for k in phase2a_breakdown} if phase2a_breakdown else {})},
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

    freshness_adjustment = _freshness_score(freshness_summary)
    if _phase2a_status(offer_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.10)
    if _phase2a_status(tariff_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.08)
    if _phase2a_status(freight_evidence.get("freshness_status")) in {"stale", "expired"}:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.08)
    if availability_evidence.get("feasible") is False:
        freshness_adjustment = max(0.0, freshness_adjustment - 0.15)

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


def _lt(vendor_lead_time, target_lead_time):
    if not vendor_lead_time or not target_lead_time:
        return 0.5
    ratio = float(vendor_lead_time) / float(target_lead_time)
    if ratio <= 0.5:
        return 1.0
    if ratio >= 2.0:
        return 0.0
    return max(0.0, 1.0 - (ratio - 0.5) / 1.5)


def _logfit(regions_served, delivery_region):
    if not delivery_region or not regions_served:
        return 0.5
    destination = delivery_region.lower()
    return 1.0 if any(destination in str(region).lower() for region in regions_served) else 0.3


def _comp(vendor_certs, required_certs):
    if not required_certs:
        return 1.0
    vendor_set = {str(cert).upper() for cert in vendor_certs}
    return sum(1 for cert in required_certs if str(cert).upper() in vendor_set) / len(required_certs)


def _capac(capacity_profile, quantity):
    if not quantity or not capacity_profile:
        return 0.5
    max_units = capacity_profile.get("max_monthly_units", 0)
    if max_units <= 0:
        return 0.5
    if float(quantity) <= max_units * 0.5:
        return 1.0
    if float(quantity) > max_units:
        return 0.1
    return 0.6

