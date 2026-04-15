from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.outcomes import AnomalyFlag, OverrideEvent, VendorPerformance
from app.models.vendor import Vendor
from app.services.outcome_informed_scoring_service import outcome_informed_scoring_service
from app.services.runtime_pipeline import runtime_pipeline_service
from app.services.scoring.vendor_scorer import rank_vendors, score_vendor


def _make_bom(db_session, test_org):
    row = BOM(
        organization_id=test_org.id,
        source_file_name="phase2c-batch2c5.csv",
        status="INGESTED",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_part(db_session, test_org, bom, row_number: int = 1, procurement_class: str = "electronics"):
    row = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="SCORED",
        row_number=row_number,
        item_id=f"ITEM-{row_number}",
        description=f"Part {row_number}",
        quantity=Decimal("5"),
        procurement_class=procurement_class,
        category_code=procurement_class.upper(),
        material="copper",
        canonical_part_key=f"{procurement_class}-line-{row_number}",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_vendor(db_session, name: str):
    row = Vendor(
        name=name,
        status="BASIC",
        country="US",
        is_active=True,
        avg_lead_time_days=Decimal("12"),
        reliability_score=Decimal("0.85"),
        regions_served=["Dallas", "USA"],
        certifications=["ISO9001"],
        capacity_profile={"monthly_capacity": 10000},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _requirements() -> dict:
    return {
        "processes": ["electronics"],
        "materials": ["copper"],
        "target_lead_time_days": 30,
        "delivery_region": "Dallas",
        "required_certifications": ["ISO9001"],
        "total_quantity": 120,
    }


def _vendor_payload(vendor: Vendor, *, price: float = 10.0, lead_days: float = 12.0) -> dict:
    return {
        "id": vendor.id,
        "name": vendor.name,
        "typical_unit_price": price,
        "avg_lead_time_days": lead_days,
        "reliability_score": 0.85,
        "regions_served": ["Dallas", "USA"],
        "certifications": ["ISO9001"],
        "capacity_profile": {"monthly_capacity": 10000},
        "capabilities": [{"process": "electronics", "material_family": "copper"}],
    }


def test_vendor_performance_affects_score_and_ranking(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor_a = _make_vendor(db_session, "Vendor A")
    vendor_b = _make_vendor(db_session, "Vendor B")

    db_session.add(VendorPerformance(
        vendor_id=vendor_a.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        on_time_rate=Decimal("0.95"),
        avg_lead_time=Decimal("9"),
        lead_time_variance=Decimal("1.25"),
        price_variance=Decimal("0.10"),
        po_win_rate=Decimal("0.70"),
        source_metadata={"quote_outcome_count": 8, "lead_time_history_count": 8, "issue_rate": 0.0},
    ))
    db_session.add(VendorPerformance(
        vendor_id=vendor_b.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        on_time_rate=Decimal("0.45"),
        avg_lead_time=Decimal("14"),
        lead_time_variance=Decimal("24.00"),
        price_variance=Decimal("5.10"),
        po_win_rate=Decimal("0.20"),
        source_metadata={"quote_outcome_count": 8, "lead_time_history_count": 8, "issue_rate": 0.25},
    ))
    db_session.flush()

    intel_a = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_a.id, bom_line_id=part.id, anomaly_summary={}).to_dict()
    intel_b = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_b.id, bom_line_id=part.id, anomaly_summary={}).to_dict()

    ranked = rank_vendors(
        [_vendor_payload(vendor_a), _vendor_payload(vendor_b)],
        requirements=_requirements(),
        market_ctx={
            "market_median_price": 10.0,
            "data_age_days": 2,
            "outcome_intelligence_by_vendor": {
                vendor_a.id: intel_a,
                vendor_b.id: intel_b,
            },
        },
    )

    assert intel_a["score_adjustment"] > 0
    assert intel_b["score_adjustment"] < 0
    assert ranked[0]["vendor_id"] == vendor_a.id


def test_lead_time_adjustment_influences_confidence_and_ranking(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor_fast = _make_vendor(db_session, "Fast Vendor")
    vendor_slow = _make_vendor(db_session, "Slow Vendor")

    db_session.add(VendorPerformance(
        vendor_id=vendor_fast.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        on_time_rate=Decimal("0.92"),
        avg_lead_time=Decimal("8"),
        lead_time_variance=Decimal("1.00"),
        price_variance=Decimal("0.20"),
        po_win_rate=Decimal("0.55"),
        source_metadata={"quote_outcome_count": 6, "lead_time_history_count": 6},
    ))
    db_session.add(VendorPerformance(
        vendor_id=vendor_slow.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        on_time_rate=Decimal("0.40"),
        avg_lead_time=Decimal("18"),
        lead_time_variance=Decimal("20.00"),
        price_variance=Decimal("0.20"),
        po_win_rate=Decimal("0.55"),
        source_metadata={"quote_outcome_count": 6, "lead_time_history_count": 6},
    ))
    db_session.flush()

    fast_adj = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_fast.id, bom_line_id=part.id, anomaly_summary={}, adjusted_lead_time_days=8).to_dict()
    slow_adj = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_slow.id, bom_line_id=part.id, anomaly_summary={}, adjusted_lead_time_days=18).to_dict()

    ranked = rank_vendors(
        [_vendor_payload(vendor_fast, lead_days=8), _vendor_payload(vendor_slow, lead_days=18)],
        requirements=_requirements(),
        market_ctx={
            "market_median_price": 10.0,
            "data_age_days": 2,
            "outcome_intelligence_by_vendor": {
                vendor_fast.id: fast_adj,
                vendor_slow.id: slow_adj,
            },
        },
    )

    assert fast_adj["confidence_adjustment"] > 0
    assert slow_adj["confidence_adjustment"] < 0
    assert ranked[0]["vendor_id"] == vendor_fast.id


def test_override_penalties_apply_conservatively_for_similar_lines(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor_a = _make_vendor(db_session, "Vendor A")
    vendor_b = _make_vendor(db_session, "Vendor B")

    for idx in range(2, 5):
        similar_part = _make_part(db_session, test_org, bom, row_number=idx)
        db_session.add(OverrideEvent(
            event_id=f"override-{idx}",
            user_id=test_user.id,
            bom_line_id=similar_part.id,
            recommended_vendor_id=vendor_a.id,
            chosen_vendor_id=vendor_b.id,
            override_reason_code="better_operational_fit",
            timestamp=datetime(2026, 4, idx, tzinfo=timezone.utc),
            source_metadata={"reason": "test"},
        ))
    db_session.flush()

    adj_a = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_a.id, bom_line_id=part.id, anomaly_summary={}).to_dict()
    adj_b = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor_b.id, bom_line_id=part.id, anomaly_summary={}).to_dict()

    ranked = rank_vendors(
        [_vendor_payload(vendor_a), _vendor_payload(vendor_b)],
        requirements=_requirements(),
        market_ctx={
            "market_median_price": 10.0,
            "data_age_days": 2,
            "outcome_intelligence_by_vendor": {
                vendor_a.id: adj_a,
                vendor_b.id: adj_b,
            },
        },
    )

    assert adj_a["override_adjustment"]["override_rate"] == 1.0
    assert adj_a["score_adjustment"] < 0
    assert ranked[0]["vendor_id"] == vendor_b.id


def test_anomaly_aware_damping_reduces_price_influence_and_flags_rfq_bias(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session, "Vendor A")

    db_session.add(AnomalyFlag(
        anomaly_id="anomaly-price-1",
        entity_type="quote_outcome",
        entity_id="row-1",
        metric_name="quoted_price",
        observed_value=Decimal("200"),
        threshold_value=Decimal("100"),
        anomaly_type="price_outlier",
        severity="high",
        source_context_json={"bom_line_id": part.id, "vendor_id": vendor.id},
        dedupe_window_key="price-1",
    ))
    db_session.add(AnomalyFlag(
        anomaly_id="anomaly-availability-1",
        entity_type="availability_snapshot",
        entity_id="row-2",
        metric_name="availability_status",
        observed_value=None,
        threshold_value=None,
        anomaly_type="availability_contradiction",
        severity="medium",
        source_context_json={"bom_line_id": part.id, "vendor_id": vendor.id},
        dedupe_window_key="availability-1",
    ))
    db_session.flush()

    summary = runtime_pipeline_service._build_anomaly_summary(db=db_session, bom_line_id=part.id, vendor_id=vendor.id)
    adjustment = outcome_informed_scoring_service.build_adjustment(db_session, vendor_id=vendor.id, bom_line_id=part.id, anomaly_summary=summary).to_dict()
    result = score_vendor(
        _vendor_payload(vendor, price=8.0),
        _requirements(),
        {
            "market_median_price": 10.0,
            "data_age_days": 2,
            "outcome_intelligence_by_vendor": {vendor.id: adjustment},
        },
    )

    assert summary["price"]["count"] == 1
    assert summary["availability"]["count"] == 1
    assert adjustment["strategy_gate_bias"] == "rfq-first"
    assert result["outcome_adjustment"]["price_damping_factor"] < 1.0
    assert result["outcome_intelligence_used"] is True


def test_fallback_when_phase2c_data_missing_preserves_existing_scoring_behavior(db_session):
    vendor = {
        "id": "vendor-1",
        "name": "Vendor 1",
        "typical_unit_price": 9.5,
        "avg_lead_time_days": 12,
        "reliability_score": 0.92,
        "regions_served": ["Dallas", "USA"],
        "certifications": ["ISO9001"],
        "capacity_profile": {"monthly_capacity": 10000},
        "capabilities": [{"process": "electronics", "material_family": "copper"}],
    }
    market_ctx = {"market_median_price": 10.0, "data_age_days": 2}

    result = score_vendor(vendor, _requirements(), market_ctx)

    assert result["phase2a_used"] is False
    assert result["outcome_intelligence_used"] is False
    assert result["outcome_adjustment"]["score_adjustment"] == 0.0
    assert "vendor_performance" not in result["breakdown"]