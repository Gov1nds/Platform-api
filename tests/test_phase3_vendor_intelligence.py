"""
Phase 3 — Vendor Intelligence test suite.

Covers every new service added under the Phase-3 directive:
  - vendor_intelligence_service (trust tier, fingerprint, dedup, merge,
    validation, batch sweep)
  - part_vendor_matcher (exact/partial/RFQ-first/award-ready, evidence boost)
  - geo_tier_service (Kerala requester, bucketing, logistics profile)
  - sourcing_strategy_engine (three strategies, landed cost, narrative)
  - commodity_price_service (buy valley, rising-trend adjustment)
  - market_anomaly_service (zero lead time, near-zero price, spike)
  - decision_safety_service (RFQ for UNVERIFIED, suspicious price, chronic,
    human-review threshold)
  - vendor_csv_ingestion_service (CSV create + duplicate fingerprint)
  - feedback_loop_service (override record, evidence-threshold guard,
    override pattern detection)
  - vendor_scorer (bounded influence, trust-tier multiplier, anomaly penalty,
    three-strategy + confidence classification)
  - end-to-end recommendation output contains three strategies with RFQ flag

All tests use the existing tests/conftest.py db_session fixture against SQLite.
Postgres-specific features (server defaults, pg_trgm) are avoided — the model
defaults and Python-side logic cover the tested surface.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.feedback import LearningEvent, RecommendationOverride
from app.models.market_intelligence import (
    CommodityPriceSignal, MarketAnomalyEvent,
)
from app.models.matching import PartVendorIndex
from app.models.vendor import (
    Vendor, VendorCapability, VendorLeadTimeBand, VendorLocation,
    VendorPerformanceSnapshot, VendorTrustTier,
)
from app.services.decision_safety_service import (
    R_CHRONIC, R_HUMAN_REVIEW, R_LOW_PRICE, R_MISSING, R_NO_HISTORY,
    decision_safety_service,
)
from app.services.learning.feedback_loop_service import feedback_loop_service
from app.services.market.commodity_price_service import commodity_price_service
from app.services.market.market_anomaly_service import market_anomaly_service
from app.services.matching.part_vendor_matcher import part_vendor_matcher
from app.services.regional.geo_tier_service import geo_tier_service
from app.services.regional.sourcing_strategy_engine import (
    SourcingStrategyEngine, sourcing_strategy_engine,
)
from app.services.scoring.vendor_scorer import (
    BOUNDED_INFLUENCE_CAP, TRUST_TIER_SCORE_MULTIPLIER,
    classify_confidence, score_vendor_with_strategy,
)
from app.services.vendor_intelligence_service import vendor_intelligence_service


# ─────────────────────────────────────────────────────────────────────────────
# Local factory helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_vendor(
    db: Session,
    *,
    name: str = "Test Vendor",
    country: str = "IN",
    region: str = "KL",
    trust_tier: str = "UNVERIFIED",
    reliability: float = 0.8,
    email: str | None = "vendor@example.com",
    phone: str | None = "+91-9999-000000",
    avg_lead_time_days: float | None = 10.0,
    primary_category: str | None = "cnc_machining",
    export_capable: bool = False,
    certifications: list[str] | None = None,
) -> Vendor:
    v = Vendor(
        name=name,
        country=country,
        region=region,
        contact_email=email,
        contact_phone=phone,
        reliability_score=Decimal(str(reliability)),
        avg_lead_time_days=Decimal(str(avg_lead_time_days)) if avg_lead_time_days else None,
        primary_category_tag=primary_category,
        trust_tier=trust_tier,
        export_capable=export_capable,
        is_active=True,
        certifications=list(certifications or []),
    )
    db.add(v)
    db.flush()
    return v


def _add_primary_location(
    db: Session, vendor: Vendor, country_iso2: str = "IN", state_province: str = "KL",
    city: str = "Kochi", is_export_office: bool = False,
) -> VendorLocation:
    loc = VendorLocation(
        vendor_id=vendor.id,
        label="headquarters",
        city=city,
        state_province=state_province,
        country_iso2=country_iso2,
        is_primary=True,
        is_export_office=is_export_office,
    )
    db.add(loc)
    db.flush()
    return loc


def _add_lead_time_band(
    db: Session, vendor: Vendor, category_tag: str = "cnc_machining",
    typical_days: float = 12.0, moq: float = 25,
) -> VendorLeadTimeBand:
    band = VendorLeadTimeBand(
        vendor_id=vendor.id,
        category_tag=category_tag,
        material_family="steel",
        moq=Decimal(str(moq)),
        lead_time_min_days=int(typical_days) - 3,
        lead_time_max_days=int(typical_days) + 3,
        lead_time_typical_days=Decimal(str(typical_days)),
        confidence=Decimal("0.8"),
        source="self_reported",
    )
    db.add(band)
    db.flush()
    return band


def _add_capability(
    db: Session, vendor: Vendor, process: str = "cnc_machining",
    material_family: str = "steel",
) -> VendorCapability:
    cap = VendorCapability(
        vendor_id=vendor.id,
        process=process,
        material_family=material_family,
        proficiency=Decimal("0.9"),
        is_active=True,
    )
    db.add(cap)
    db.flush()
    return cap


def _add_performance_snapshot(
    db: Session, vendor: Vendor, total_pos: int = 10,
    on_time_pct: float = 0.9, quality_pct: float = 0.95,
) -> VendorPerformanceSnapshot:
    snap = VendorPerformanceSnapshot(
        vendor_id=vendor.id,
        snapshot_date=date.today(),
        total_pos=total_pos,
        on_time_delivery_pct=Decimal(str(on_time_pct)),
        quality_pass_pct=Decimal(str(quality_pct)),
    )
    db.add(snap)
    db.flush()
    return snap


# ─────────────────────────────────────────────────────────────────────────────
# Trust tier / validation / fingerprint / dedup / merge
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_trust_tier_platinum(db_session: Session):
    v = _make_vendor(db_session, name="Acme Precision Co", reliability=0.92)
    _add_primary_location(db_session, v)
    _add_capability(db_session, v)
    _add_lead_time_band(db_session, v)
    _add_performance_snapshot(db_session, v, total_pos=25, on_time_pct=0.95, quality_pct=0.95)
    # Fill optional fields to raise completeness to ≥0.90
    v.legal_name = "Acme Precision Co Pvt Ltd"
    v.website = "https://acme.example.com"
    v.trade_name = "Acme"
    v.founded_year = 2005
    v.employee_count_band = "50-200"
    v.certifications = ["ISO9001", "AS9100"]
    v.secondary_category_tags = ["sheet_metal", "assembly"]
    v.export_capable = True
    db_session.flush()

    result = vendor_intelligence_service.compute_trust_tier(v.id, db_session)
    assert result.tier == "PLATINUM"
    assert result.data_completeness_score >= 0.90
    assert result.reliability_score >= 0.90
    assert not result.missing_required_fields


def test_compute_trust_tier_unverified_when_required_missing(db_session: Session):
    v = _make_vendor(db_session, name="Sparse Co", email=None, phone=None, primary_category=None, avg_lead_time_days=None)
    # No location, no capability, no contact info, no lead time, no category.
    result = vendor_intelligence_service.compute_trust_tier(v.id, db_session)
    assert result.tier == "UNVERIFIED"
    # We expect multiple required fields to be reported missing
    assert {"contact", "primary_category_tag", "lead_time_band", "primary_location"} <= set(result.missing_required_fields)


def test_validate_vendor_profile_flags_missing_location(db_session: Session):
    v = _make_vendor(db_session, name="No Loc Co", email="x@y.com", phone=None)
    v.country = None  # no implicit country
    db_session.flush()
    result = vendor_intelligence_service.validate_vendor_profile(v.id, db_session)
    assert not result.ok
    assert "no_locations_on_record" in result.errors or "no_country_on_any_location" in result.errors


def test_dedup_fingerprint_normalizes_legal_suffixes():
    fp1 = vendor_intelligence_service.compute_dedup_fingerprint(
        {"name": "Acme Ltd.", "country_iso2": "IN", "contact_email": "sales@acme.in"}
    )
    fp2 = vendor_intelligence_service.compute_dedup_fingerprint(
        {"name": "ACME LIMITED", "country_iso2": "in", "contact_email": "support@acme.in"}
    )
    # Same normalized name + country + email domain -> same fingerprint
    assert fp1 == fp2


def test_dedup_fingerprint_differs_for_different_countries():
    fp1 = vendor_intelligence_service.compute_dedup_fingerprint(
        {"name": "Acme Ltd", "country_iso2": "IN", "contact_email": None}
    )
    fp2 = vendor_intelligence_service.compute_dedup_fingerprint(
        {"name": "Acme Ltd", "country_iso2": "US", "contact_email": None}
    )
    assert fp1 != fp2


def test_find_duplicate_candidates_returns_high_similarity_pairs(db_session: Session):
    a = _make_vendor(db_session, name="Acme Precision Private Limited", country="IN", region="KL")
    b = _make_vendor(db_session, name="Acme Precision Pvt Ltd", country="IN", region="KL")
    vendor_intelligence_service.refresh_vendor_fingerprint(db_session, a)
    vendor_intelligence_service.refresh_vendor_fingerprint(db_session, b)

    candidates = vendor_intelligence_service.find_duplicate_candidates(a.id, db_session)
    assert any(c.candidate_vendor_id == b.id for c in candidates)


def test_merge_vendor_duplicates_moves_children_and_soft_deletes(db_session: Session):
    primary = _make_vendor(db_session, name="Primary Co")
    duplicate = _make_vendor(db_session, name="Duplicate Co")
    _add_primary_location(db_session, duplicate)
    _add_capability(db_session, duplicate, process="machining", material_family="steel")
    _add_lead_time_band(db_session, duplicate)

    summary = vendor_intelligence_service.merge_vendor_duplicates(
        primary.id, duplicate.id, db_session,
    )
    assert summary["primary_vendor_id"] == primary.id
    assert summary["moved"]["capabilities"] >= 1
    assert summary["moved"]["locations"] >= 1
    assert summary["moved"]["lead_time_bands"] >= 1

    db_session.refresh(duplicate)
    assert duplicate.merged_into_vendor_id == primary.id
    assert duplicate.is_active is False


# ─────────────────────────────────────────────────────────────────────────────
# Part → Vendor matcher
# ─────────────────────────────────────────────────────────────────────────────


def test_part_vendor_matcher_exact_match_scores_high(db_session: Session):
    v = _make_vendor(db_session, name="Exact Match Co", trust_tier="GOLD")
    _add_capability(db_session, v, process="cnc_machining", material_family="steel")
    results = part_vendor_matcher.match_canonical_part(
        canonical_part={
            "canonical_part_key": "part-1",
            "category_tag": "cnc_machining",
            "material_family": "steel",
            "processes": ["cnc_machining"],
        },
        db=db_session,
    )
    r = next(r for r in results if r.vendor_id == v.id)
    assert r.match_type == "exact_category"
    assert r.match_score > 0.80  # GOLD×1.0 = 0.95 before any boost


def test_part_vendor_matcher_rfq_first_when_weak_match(db_session: Session):
    v = _make_vendor(db_session, name="Generic Co", trust_tier="UNVERIFIED")
    _add_capability(db_session, v, process="other_process", material_family="plastic_resin")
    results = part_vendor_matcher.match_canonical_part(
        canonical_part={
            "canonical_part_key": "part-weak",
            "category_tag": "cnc_machining",
            "material_family": "steel",
            "processes": ["cnc_machining"],
        },
        db=db_session,
    )
    # With UNVERIFIED + no overlap, either no result or rfq_first=True
    for r in results:
        if r.vendor_id == v.id:
            assert r.rfq_first_recommended is True
            assert r.award_ready is False


def test_part_vendor_matcher_award_ready_requires_multiple_indicators(db_session: Session):
    # Even a well-matched GOLD vendor lacking fresh quote history must not be award_ready
    v = _make_vendor(db_session, name="Strong Cap Only", trust_tier="GOLD", reliability=0.85)
    _add_capability(db_session, v, process="cnc_machining", material_family="steel")
    _add_lead_time_band(db_session, v)
    results = part_vendor_matcher.match_canonical_part(
        canonical_part={
            "canonical_part_key": "no-quote-history",
            "category_tag": "cnc_machining",
            "material_family": "steel",
            "processes": ["cnc_machining"],
        },
        db=db_session,
    )
    r = next(r for r in results if r.vendor_id == v.id)
    assert r.award_ready is False
    # Reason list should include missing quote history
    reasons = r.historical.get("award_ready_gate_reasons", [])
    assert "no_fresh_quote_in_180_days" in reasons
    assert "evidence_count_below_2" in reasons


def test_update_index_from_outcome_builds_award_ready(db_session: Session):
    v = _make_vendor(db_session, name="Evidence Growing Co", trust_tier="GOLD", reliability=0.85)
    _add_capability(db_session, v)
    _add_lead_time_band(db_session, v)

    # Seed match
    part_vendor_matcher.match_canonical_part(
        canonical_part={
            "canonical_part_key": "pkey-1",
            "category_tag": "cnc_machining",
            "material_family": "steel",
            "processes": ["cnc_machining"],
        },
        db=db_session,
    )
    # Record RFQ response + PO award to build evidence
    part_vendor_matcher.update_index_from_outcome(
        canonical_part_key="pkey-1", vendor_id=v.id,
        outcome_type="rfq_response",
        outcome_data={"quoted_price": "100", "currency": "USD", "quote_date": date.today().isoformat()},
        db=db_session,
    )
    part_vendor_matcher.update_index_from_outcome(
        canonical_part_key="pkey-1", vendor_id=v.id,
        outcome_type="po_awarded",
        outcome_data={"po_date": date.today().isoformat()},
        db=db_session,
    )
    row = db_session.query(PartVendorIndex).filter(
        PartVendorIndex.canonical_part_key == "pkey-1",
        PartVendorIndex.vendor_id == v.id,
    ).first()
    assert row.evidence_count >= 2
    assert row.po_win_count >= 1
    assert row.last_quote_price == Decimal("100")


# ─────────────────────────────────────────────────────────────────────────────
# Geo tier service
# ─────────────────────────────────────────────────────────────────────────────


def test_geo_tier_service_kerala_requester_local_regional_national():
    ctx = geo_tier_service.classify_requester_location(
        {"country": "India", "state": "Kerala", "city": "Kochi"}
    )
    assert ctx.country_iso2 == "IN"
    assert ctx.local_state == "KL"
    assert "TN" in ctx.regional_states
    assert "KA" in ctx.regional_states
    assert ctx.national_country == "IN"


def test_geo_tier_service_bucket_vendors_correctly():
    ctx = geo_tier_service.classify_requester_location(
        {"country_iso2": "IN", "state_province": "KL"}
    )
    vendors = [
        {"id": "v1", "country": "IN", "region": "KL", "locations": [
            {"country_iso2": "IN", "state_province": "KL", "is_primary": True}]},
        {"id": "v2", "country": "IN", "region": "TN", "locations": [
            {"country_iso2": "IN", "state_province": "TN", "is_primary": True}]},
        {"id": "v3", "country": "IN", "region": "DL", "locations": [
            {"country_iso2": "IN", "state_province": "DL", "is_primary": True}]},
        {"id": "v4", "country": "VN", "region": "BD", "export_capable": True, "locations": [
            {"country_iso2": "VN", "state_province": "BD", "is_primary": True}]},
        {"id": "v5", "country": "VN", "region": "BD", "export_capable": False, "locations": [
            {"country_iso2": "VN", "state_province": "BD", "is_primary": True}]},
    ]
    buckets = geo_tier_service.bucket_vendors_by_geo_tier(vendors, ctx)
    assert [v["id"] for v in buckets.local] == ["v1"]
    assert [v["id"] for v in buckets.regional] == ["v2"]
    assert [v["id"] for v in buckets.national] == ["v3"]
    assert [v["id"] for v in buckets.global_] == ["v4"]  # non-export-capable v5 dropped


def test_geo_tier_logistics_profile_tiers_differ():
    ctx = geo_tier_service.classify_requester_location({"country_iso2": "IN", "state_province": "KL"})
    local = geo_tier_service.compute_logistics_profile(
        {"id": "v1", "geo_tier": "local"}, ctx,
    )
    global_ = geo_tier_service.compute_logistics_profile(
        {"id": "v2", "geo_tier": "global"}, ctx,
    )
    assert local.est_transit_days_max < global_.est_transit_days_max
    assert local.est_freight_cost_usd < global_.est_freight_cost_usd


# ─────────────────────────────────────────────────────────────────────────────
# Sourcing strategy engine
# ─────────────────────────────────────────────────────────────────────────────


def test_sourcing_strategy_engine_generates_three_strategies(db_session: Session):
    ctx = geo_tier_service.classify_requester_location({"country_iso2": "IN", "state_province": "KL"})

    def _make(id_, tier, score_local, score_dom, score_landed, landed_total):
        return {
            "id": id_, "name": f"Vendor {id_}", "country": "IN", "region": "KL",
            "trust_tier": "GOLD", "geo_tier": tier,
            "strategy_scores": {
                "fastest_local": score_local,
                "best_domestic_value": score_dom,
                "lowest_landed_cost": score_landed,
            },
            "landed_cost": type("LC", (), {
                "total_landed_cost": Decimal(str(landed_total)),
                "unit_price": Decimal("100"), "quantity": Decimal("1"),
                "freight_cost": Decimal("5"), "tariff_amount": Decimal("0"),
                "fx_conversion_cost": Decimal("0"),
                "landed_cost_per_unit": Decimal(str(landed_total)), "currency": "USD",
                "breakdown_narrative": "",
            })(),
            "evidence": {"award_ready": True, "rfq_recommended": False, "evidence_count": 5},
        }

    scored = [
        _make("v-local", "local", 0.90, 0.80, 0.70, 120),
        _make("v-regional", "regional", 0.70, 0.85, 0.75, 115),
        _make("v-global", "global", 0.30, 0.55, 0.90, 95),
    ]
    from app.services.regional.geo_tier_service import GeoBuckets
    buckets = GeoBuckets(
        local=[scored[0]], regional=[scored[1]], national=[], global_=[scored[2]]
    )
    strategies = sourcing_strategy_engine.generate_sourcing_strategies(
        scored_vendors=scored, geo_buckets=buckets, geo_ctx=ctx,
        requirements={}, db=db_session,
    )
    names = [s.strategy_name for s in strategies]
    assert names == ["fastest_local", "best_domestic_value", "lowest_landed_cost"]
    # Fastest-local top should be v-local
    assert strategies[0].top_option.vendor_id == "v-local"
    # Lowest landed should pick v-global (lowest total_landed_cost)
    assert strategies[2].top_option.vendor_id == "v-global"


def test_landed_cost_includes_freight_tariff_fx():
    engine = SourcingStrategyEngine()
    logistics = type("LP", (), {"est_freight_cost_usd": Decimal("12.50")})
    result = engine.compute_landed_cost(
        vendor={"id": "v1"},
        unit_price=Decimal("10"),
        quantity=100,
        logistics_profile=logistics,
        tariff_rate=Decimal("0.05"),
        fx_rate=Decimal("80"),
        currency="INR",
    )
    # merchandise = 10*100 = 1000; tariff 5% = 50; fx spread 1.5% of 1000 = 15
    assert result.freight_cost == Decimal("12.50")
    assert result.tariff_amount == Decimal("50.00")
    assert result.fx_conversion_cost == Decimal("15.00")
    # Total = 1000 + 12.50 + 50 + 15 = 1077.50
    assert result.total_landed_cost == Decimal("1077.50")
    assert "Total landed" in result.breakdown_narrative


def test_trade_off_narrative_mentions_freight_and_tariff():
    engine = SourcingStrategyEngine()
    logistics = type("LP", (), {"est_freight_cost_usd": Decimal("200")})
    landed = engine.compute_landed_cost(
        vendor={"name": "Vendor C"}, unit_price=Decimal("50"), quantity=1,
        logistics_profile=logistics, tariff_rate=Decimal("0.05"),
        fx_rate=None, currency="USD",
    )
    narr = engine.build_trade_off_narrative(
        "lowest_landed_cost", {"name": "Vendor C", "country": "VN", "geo_tier": "global"},
        landed_cost=landed,
        comparisons={"savings_vs_domestic_pct": 12.5},
    )
    assert "Vendor C" in narr
    assert "freight" in narr.lower() or "tariff" in narr.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Commodity price service
# ─────────────────────────────────────────────────────────────────────────────


def test_commodity_price_service_buy_valley_signal(db_session: Session):
    commodity_price_service.ingest_commodity_signal(
        {
            "commodity_name": "steel_hrc",
            "material_family_tag": "steel",
            "price_per_unit": 0.80,
            "unit": "kg", "currency": "USD",
            "price_date": date.today().isoformat(),
            "is_valley": True, "trend_direction": "falling", "trend_pct_30d": -2,
        },
        db_session,
    )
    assert commodity_price_service.is_buy_valley("steel", db_session) is True


def test_commodity_price_service_adjusts_for_rising_trend(db_session: Session):
    commodity_price_service.ingest_commodity_signal(
        {
            "commodity_name": "copper_cathode",
            "material_family_tag": "copper",
            "price_per_unit": 10.0, "unit": "kg", "currency": "USD",
            "price_date": date.today().isoformat(),
            "trend_direction": "rising", "trend_pct_30d": 5.0,
            "is_valley": False,
        },
        db_session,
    )
    adjusted = commodity_price_service.adjust_vendor_price_for_commodity_trend(
        unit_price=Decimal("100"), material_family="copper", db=db_session,
    )
    # 5 % rising → adjust upward
    assert adjusted.adjusted_price > adjusted.original_price
    assert adjusted.trend_direction == "rising"


# ─────────────────────────────────────────────────────────────────────────────
# Market anomaly service
# ─────────────────────────────────────────────────────────────────────────────


def test_market_anomaly_service_flags_zero_lead_time_as_critical(db_session: Session):
    flags = market_anomaly_service.check_quote_for_anomalies(
        vendor_id=None, canonical_part_key=None,
        quoted_price=100, quoted_lead_time_days=0, db=db_session,
    )
    assert any(f.anomaly_type == "zero_lead_time" and f.severity == "CRITICAL" for f in flags)


def test_market_anomaly_service_flags_near_zero_price(db_session: Session):
    flags = market_anomaly_service.check_quote_for_anomalies(
        vendor_id=None, canonical_part_key=None,
        quoted_price=Decimal("0.05"), quoted_lead_time_days=10,
        market_median_price=Decimal("100"),
        db=db_session,
    )
    assert any(f.anomaly_type == "near_zero_price" and f.severity == "HIGH" for f in flags)


def test_market_anomaly_penalty_caps_at_zero():
    flags = [{"severity": "CRITICAL"}, {"severity": "HIGH"}]
    adjusted = market_anomaly_service.apply_anomaly_penalty_to_score(0.40, flags)
    # -0.30 + -0.15 = -0.45 → 0.40 + (-0.45) = -0.05 → floor 0
    assert adjusted == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Decision safety
# ─────────────────────────────────────────────────────────────────────────────


def _make_strategy(vendor_id, *, score=0.7, confidence="MEDIUM", confidence_score=0.7,
                   rfq_recommended=False, runners=None):
    from app.services.regional.sourcing_strategy_engine import (
        StrategyOption, SourcingStrategy,
    )
    opt = StrategyOption(
        vendor_id=vendor_id, vendor_name="X", vendor_country="IN",
        vendor_state_region="KL", geo_tier="local", rank_within_strategy=1,
        strategy_score=score, confidence=confidence, confidence_score=confidence_score,
        unit_price=None, freight_cost=None, tariff_amount=None,
        fx_conversion_cost=None, landed_cost_total=None,
        landed_cost_per_unit=None, currency="USD",
        lead_time_min_days=None, lead_time_max_days=None,
        lead_time_typical_days=None, lead_time_reliability_score=None,
        award_ready=not rfq_recommended, rfq_recommended=rfq_recommended,
        rationale_narrative="", trade_off_narrative="",
    )
    return SourcingStrategy(
        strategy_name="fastest_local", strategy_label="Fastest Local Option",
        top_option=opt, runner_up_options=runners or [],
        strategy_confidence=confidence, rfq_required=rfq_recommended,
        strategy_narrative="", geo_tier_context={},
    )


def test_decision_safety_rfq_for_unverified_vendor():
    strategy = _make_strategy("v-un", score=0.80, confidence="LOW", confidence_score=0.5)
    report = decision_safety_service.evaluate_recommendation_safety(
        strategy_results=[strategy],
        vendor_scores={
            "v-un": {
                "trust_tier": "UNVERIFIED", "evidence_count": 0, "total_pos": 0,
                "match_score": 0.80, "price_competitiveness_score": 0.5,
                "confidence_score": 0.5, "anomaly_flags": [],
            }
        },
    )
    assert "v-un" in report.rfq_required_vendors
    assert R_MISSING in report.risk_flags.get("v-un", []) or R_NO_HISTORY in report.risk_flags.get("v-un", [])


def test_decision_safety_flags_suspiciously_low_price():
    strategy = _make_strategy("v-low", score=0.85, confidence="MEDIUM", confidence_score=0.75)
    report = decision_safety_service.evaluate_recommendation_safety(
        strategy_results=[strategy],
        vendor_scores={
            "v-low": {
                "trust_tier": "GOLD", "evidence_count": 5, "total_pos": 5,
                "on_time_delivery_pct": 0.9,
                "match_score": 0.85, "price_competitiveness_score": 1.0,
                "confidence_score": 0.75,
                "anomaly_flags": [{"severity": "HIGH", "anomaly_type": "near_zero_price"}],
            }
        },
    )
    assert R_LOW_PRICE in report.risk_flags.get("v-low", [])
    assert report.human_review_required is True


def test_decision_safety_demotes_chronic_underperformer():
    strategy = _make_strategy("v-chronic", score=0.60, confidence="MEDIUM", confidence_score=0.6)
    report = decision_safety_service.evaluate_recommendation_safety(
        strategy_results=[strategy],
        vendor_scores={
            "v-chronic": {
                "trust_tier": "SILVER", "evidence_count": 5, "total_pos": 10,
                "on_time_delivery_pct": 0.45,
                "match_score": 0.60, "price_competitiveness_score": 0.6,
                "confidence_score": 0.6, "anomaly_flags": [],
            }
        },
    )
    assert R_CHRONIC in report.risk_flags.get("v-chronic", [])


def test_decision_safety_human_review_for_low_confidence():
    strategy = _make_strategy("v-low-conf", score=0.30, confidence="LOW", confidence_score=0.30)
    report = decision_safety_service.evaluate_recommendation_safety(
        strategy_results=[strategy],
        vendor_scores={
            "v-low-conf": {
                "trust_tier": "BRONZE", "evidence_count": 2, "total_pos": 2,
                "on_time_delivery_pct": 0.8,
                "match_score": 0.30, "price_competitiveness_score": 0.5,
                "confidence_score": 0.30, "anomaly_flags": [],
            }
        },
    )
    assert report.human_review_required is True
    assert R_HUMAN_REVIEW in report.risk_flags.get("v-low-conf", [])


# ─────────────────────────────────────────────────────────────────────────────
# CSV ingestion
# ─────────────────────────────────────────────────────────────────────────────


def test_vendor_csv_ingestion_creates_vendor_with_location(db_session: Session):
    from app.services.ingestion.vendor_csv_ingestion_service import (
        vendor_csv_ingestion_service,
    )
    csv_text = (
        "name,legal_name,country,state_province,city,contact_email,"
        "primary_category_tag,avg_lead_time_days\n"
        "Kerala Precision Works,Kerala Precision Works Pvt Ltd,IN,KL,Kochi,"
        "sales@keralaprecision.in,cnc_machining,12\n"
    )
    result = vendor_csv_ingestion_service.ingest_vendor_csv(
        file_content=csv_text, org_id=None, created_by_user_id=None,
        db=db_session, file_name="test.csv",
    )
    assert result.total_rows == 1
    assert result.success == 1
    vendor_id = result.row_results[0].vendor_id
    assert vendor_id is not None
    v = db_session.query(Vendor).filter(Vendor.id == vendor_id).first()
    assert v is not None
    assert v.locations  # primary location auto-created


def test_vendor_csv_ingestion_detects_duplicate_fingerprint(db_session: Session):
    from app.services.ingestion.vendor_csv_ingestion_service import (
        vendor_csv_ingestion_service,
    )
    csv_text = (
        "name,country,contact_email,primary_category_tag\n"
        "Acme Ltd,IN,sales@acme.in,cnc_machining\n"
        "Acme Limited,IN,support@acme.in,cnc_machining\n"
    )
    result = vendor_csv_ingestion_service.ingest_vendor_csv(
        file_content=csv_text, org_id=None, created_by_user_id=None,
        db=db_session, file_name="dup.csv",
    )
    assert result.total_rows == 2
    # Same fingerprint → second row updates the first OR flags duplicate
    warnings_found = any(
        r.warnings for r in result.row_results
    ) or any(r.duplicate_of for r in result.row_results)
    assert warnings_found or len({r.vendor_id for r in result.row_results if r.vendor_id}) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Feedback loop
# ─────────────────────────────────────────────────────────────────────────────


def test_feedback_loop_records_override(db_session: Session, test_project):
    v_rec = _make_vendor(db_session, name="Recommended Co")
    v_over = _make_vendor(db_session, name="Override Co")
    feedback_loop_service.record_user_override(
        project_id=test_project.id,
        bom_part_id=None,
        canonical_part_key="pkey-x",
        recommended_vendor_id=v_rec.id,
        override_vendor_id=v_over.id,
        override_reason="preferred existing supplier",
        override_by_user_id=None,
        db=db_session,
        strategy_at_time="fastest_local",
        score_at_time=0.82,
    )
    rec = db_session.query(RecommendationOverride).filter(
        RecommendationOverride.project_id == test_project.id,
    ).first()
    assert rec is not None
    assert rec.override_vendor_id == v_over.id
    # Also a learning_event should have been recorded
    events = db_session.query(LearningEvent).filter(
        LearningEvent.event_type == "user_override",
    ).all()
    assert events


def test_feedback_loop_guards_against_low_evidence_score_change():
    check = feedback_loop_service.evaluate_score_adjustment_safety(
        vendor_id="v1", proposed_adjustment=0.25, evidence_count=2, trigger="outcome",
    )
    assert check.approved is False
    # Evidence 2 < 3 so even 0.25 (>0.10) is rejected
    assert "insufficient_evidence" in check.reason


def test_feedback_loop_large_adjustment_requires_human_review():
    check = feedback_loop_service.evaluate_score_adjustment_safety(
        vendor_id="v1", proposed_adjustment=0.40, evidence_count=20, trigger="outcome",
    )
    assert check.approved is False
    assert check.human_review_required is True


def test_feedback_loop_within_thresholds_approved():
    check = feedback_loop_service.evaluate_score_adjustment_safety(
        vendor_id="v1", proposed_adjustment=0.08, evidence_count=5, trigger="outcome",
    )
    assert check.approved is True
    assert check.capped_adjustment == 0.08


# ─────────────────────────────────────────────────────────────────────────────
# Scoring — bounded influence, tier multiplier, anomaly penalty
# ─────────────────────────────────────────────────────────────────────────────


def test_score_vendor_trust_tier_multiplier_applied():
    # Same vendor, different tiers → different final scores.
    req = {"processes": ["machining"], "materials": ["steel"],
           "target_lead_time_days": 20, "delivery_region": "Kerala",
           "required_certifications": [], "total_quantity": 100}
    mkt = {"market_median_price": 100.0, "data_age_days": 5,
           "tariff_rate": 0.0, "landed_cost_total": 100.0,
           "market_median_landed": 100.0, "anomaly_flags": [],
           "evidence_context": {"evidence_count": 3}}
    base = {
        "id": "v1", "name": "Vendor A", "reliability_score": 0.85,
        "avg_lead_time_days": 10, "regions_served": ["Kerala"],
        "certifications": [], "capacity_profile": {"monthly_capacity": 1000},
        "capabilities": [{"process": "machining", "material_family": "steel"}],
        "typical_unit_price": 100.0,
    }
    plat = score_vendor_with_strategy({**base, "trust_tier": "PLATINUM"}, req, mkt, strategy="balanced")
    unverified = score_vendor_with_strategy({**base, "trust_tier": "UNVERIFIED"}, req, mkt, strategy="balanced")
    assert plat["total_score"] > unverified["total_score"]
    assert plat["trust_tier_multiplier"] == TRUST_TIER_SCORE_MULTIPLIER["PLATINUM"]


def test_score_vendor_anomaly_penalty_applied():
    req = {"processes": ["machining"], "materials": ["steel"],
           "target_lead_time_days": 20, "delivery_region": "Kerala",
           "required_certifications": [], "total_quantity": 100}
    mkt_clean = {"market_median_price": 100.0, "data_age_days": 5,
                 "tariff_rate": 0.0, "anomaly_flags": [],
                 "evidence_context": {"evidence_count": 3}}
    mkt_anom = {**mkt_clean, "anomaly_flags": [{"severity": "CRITICAL"}]}
    v = {
        "id": "v1", "name": "A", "trust_tier": "GOLD",
        "reliability_score": 0.85, "avg_lead_time_days": 10,
        "regions_served": ["Kerala"], "certifications": [],
        "capacity_profile": {"monthly_capacity": 1000},
        "capabilities": [{"process": "machining", "material_family": "steel"}],
        "typical_unit_price": 100.0,
    }
    clean = score_vendor_with_strategy(v, req, mkt_clean, strategy="balanced")
    anom = score_vendor_with_strategy(v, req, mkt_anom, strategy="balanced")
    assert anom["total_score"] < clean["total_score"]
    assert anom["anomaly_penalty_applied"] < 0


def test_score_vendor_bounded_influence_cap_present():
    assert BOUNDED_INFLUENCE_CAP == 0.30


def test_classify_confidence_progression():
    # High score + PLATINUM + evidence → HIGH
    assert classify_confidence(0.85, {"trust_tier": "PLATINUM"}, {"evidence_count": 5})[0] == "HIGH"
    # Lower score + SILVER → MEDIUM
    assert classify_confidence(0.60, {"trust_tier": "SILVER"}, {"evidence_count": 2})[0] == "MEDIUM"
    # UNVERIFIED always LOW
    assert classify_confidence(0.90, {"trust_tier": "UNVERIFIED"}, {"evidence_count": 10})[0] == "LOW"


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end-ish: three-strategy output structure
# ─────────────────────────────────────────────────────────────────────────────


def test_recommendation_output_contains_three_sourcing_strategies(db_session: Session):
    """
    The sourcing engine produces exactly the three named strategies in the
    order required by the execution plan §6.
    """
    ctx = geo_tier_service.classify_requester_location({"country_iso2": "IN", "state_province": "KL"})
    from app.services.regional.geo_tier_service import GeoBuckets
    buckets = GeoBuckets(local=[], regional=[], national=[], global_=[])
    strategies = sourcing_strategy_engine.generate_sourcing_strategies(
        scored_vendors=[], geo_buckets=buckets, geo_ctx=ctx,
        requirements={}, db=db_session,
    )
    names = [s.strategy_name for s in strategies]
    assert names == ["fastest_local", "best_domestic_value", "lowest_landed_cost"]
    # All three are RFQ-required when no eligible vendors
    assert all(s.rfq_required for s in strategies)


def test_recommendation_output_includes_rfq_flag_for_weak_matches(db_session: Session):
    """
    An UNVERIFIED vendor with no capability overlap must produce
    rfq_first_recommended = True in the part-vendor index.
    """
    v = _make_vendor(db_session, name="No Match Co", trust_tier="UNVERIFIED",
                     avg_lead_time_days=None, primary_category=None)
    _add_capability(db_session, v, process="x", material_family="y")
    results = part_vendor_matcher.match_canonical_part(
        canonical_part={
            "canonical_part_key": "weak-pk",
            "category_tag": "cnc_machining",
            "material_family": "steel",
            "processes": ["cnc_machining"],
        },
        db=db_session,
    )
    # Either there is no match at all, or it is flagged as RFQ-first
    for r in results:
        if r.vendor_id == v.id:
            assert r.rfq_first_recommended is True
            assert r.award_ready is False
