from __future__ import annotations

from app.services.runtime_pipeline import runtime_pipeline_service
from app.services.scoring.vendor_scorer import score_vendor


def _candidate(vendor_id: str = "vendor-1") -> dict:
    return {
        "id": vendor_id,
        "name": "Vendor 1",
        "typical_unit_price": 9.5,
        "avg_lead_time_days": 12,
        "reliability_score": 0.92,
        "regions_served": ["Dallas", "USA"],
        "certifications": ["ISO9001"],
        "capacity_profile": {"max_monthly_units": 10000},
        "capabilities": [{"process": "electronics", "material_family": "copper"}],
    }


def _requirements() -> dict:
    return {
        "processes": ["electronics"],
        "materials": ["copper"],
        "target_lead_time_days": 30,
        "delivery_region": "Dallas",
        "required_certifications": ["ISO9001"],
        "total_quantity": 120,
    }


def test_score_vendor_uses_phase2a_evidence_when_present():
    vendor = _candidate()
    market_ctx = {
        "market_median_price": 10.0,
        "data_age_days": 2,
        "phase2a": {
            "offer_evidence": {"vendor_id": "vendor-1", "freshness_status": "FRESH"},
            "availability_evidence": {"feasible": True},
            "tariff_evidence": {"freshness_status": "FRESH"},
            "freight_evidence": {"freshness_status": "FRESH"},
            "freshness_summary": {
                "status": "fresh",
                "offer_status": "fresh",
                "availability_status": "fresh",
                "tariff_status": "fresh",
                "freight_status": "fresh",
            },
            "confidence_summary": {"score": 0.9},
            "uncertainty_flags": {
                "offer_missing": False,
                "availability_missing": False,
                "tariff_uncertain": False,
                "freight_uncertain": False,
                "hs_uncertain": False,
            },
        },
    }

    result = score_vendor(vendor, _requirements(), market_ctx)

    assert result["phase2a_used"] is True
    assert "evidence_completeness" in result["breakdown"]
    assert "evidence_confidence" in result["breakdown"]
    assert result["breakdown"]["evidence_completeness"] >= 0.9
    assert result["breakdown"]["freshness_adjustment"] >= 0.9


def test_score_vendor_keeps_phase1_shape_when_phase2a_absent():
    vendor = _candidate()
    market_ctx = {"market_median_price": 10.0, "data_age_days": 2}

    result = score_vendor(vendor, _requirements(), market_ctx)

    assert result["phase2a_used"] is False
    assert "evidence_completeness" not in result["breakdown"]
    assert "evidence_confidence" not in result["breakdown"]


def test_strategy_gate_and_confidence_drop_for_uncertain_phase2a():
    bundle = {
        "freshness_summary": {"status": "stale"},
        "confidence_summary": {"score": 0.42},
        "uncertainty_flags": {
            "offer_missing": False,
            "availability_missing": True,
            "tariff_uncertain": True,
            "freight_uncertain": False,
            "hs_uncertain": True,
        },
    }

    gate = runtime_pipeline_service._phase2a_strategy_gate(phase2a_bundle=bundle, has_recommendation=True)
    confidence_score = runtime_pipeline_service._evidence_weighted_confidence_score(
        normalized_confidence=0.88,
        freshness_status="stale",
        has_full_match=True,
        phase2a_bundle=bundle,
    )

    assert gate["strategy_gate"] == "verify-first"
    assert any("Availability evidence" in reason for reason in gate["strategy_reasons"])
    assert confidence_score < 0.6
    assert runtime_pipeline_service._confidence_label(confidence_score=confidence_score) in {"LOW", "MEDIUM"}


def test_strategy_gate_rfq_first_when_offer_missing_or_no_recommendation():
    bundle = {
        "freshness_summary": {"status": "unknown"},
        "confidence_summary": {"score": 0.0},
        "uncertainty_flags": {
            "offer_missing": True,
            "availability_missing": True,
            "tariff_uncertain": True,
            "freight_uncertain": True,
            "hs_uncertain": True,
        },
    }

    gate = runtime_pipeline_service._phase2a_strategy_gate(phase2a_bundle=bundle, has_recommendation=False)

    assert gate["strategy_gate"] == "rfq-first"
    assert any("Offer evidence is missing" in reason for reason in gate["strategy_reasons"])
