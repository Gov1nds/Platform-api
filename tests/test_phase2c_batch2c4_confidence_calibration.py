from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.outcomes import AnomalyFlag
from app.models.vendor import Vendor
from app.schemas.recommendation import VendorRankingEntry
from app.services.confidence_calibration_service import confidence_calibration_service
from app.services.outcome_data_service import outcome_data_service
from app.services.recommendation_stability_service import recommendation_stability_service


def _make_bom(db_session, test_org):
    row = BOM(
        organization_id=test_org.id,
        source_file_name="phase2c-batch2c4.csv",
        status="INGESTED",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_part(db_session, test_org, bom, row_number: int = 1):
    row = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="SCORED",
        row_number=row_number,
        item_id=f"ITEM-{row_number}",
        description=f"Part {row_number}",
        quantity=Decimal("5"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_vendor(db_session, name: str = "Vendor A"):
    row = Vendor(
        name=name,
        status="BASIC",
        country="US",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_outcome(db_session, *, part_id: str, vendor_id: str, score: str, accepted: bool, day: int):
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part_id,
        vendor_id=vendor_id,
        quoted_price=Decimal("10.00"),
        quoted_lead_time=Decimal("8"),
        is_accepted=accepted,
        quote_date=date(2026, 4, day),
        source_metadata={"recommendation_score": score},
    )


def _candidate(*, vendor_id: str, vendor_name: str, rank: int, score: float, line_total: float, freight_total: float = 10.0, availability: str = "feasible", high_anomaly: bool = False):
    return VendorRankingEntry(
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        rank=rank,
        score=score,
        confidence="MEDIUM",
        confidence_score=0.5,
        raw_confidence_score=0.5,
        calibrated_confidence_score=0.5,
        rationale="test",
        freshness_status="fresh",
        estimated_line_total=line_total,
        estimated_freight_total=freight_total,
        evidence={
            "phase2a": {"availability_evidence": {"feasible": availability == "feasible"}},
            "anomaly_summary": {"has_high_severity": high_anomaly},
        },
    )


def test_calibration_band_generation_and_mapping(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.82", accepted=True, day=1)
    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.84", accepted=True, day=2)
    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.88", accepted=False, day=3)
    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.24", accepted=False, day=4)
    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.26", accepted=False, day=5)
    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.28", accepted=True, day=6)

    rows = confidence_calibration_service.rebuild_calibration_data(db_session, minimum_sample_size=3)
    assert rows

    mapped = confidence_calibration_service.map_confidence(db_session, raw_confidence=Decimal("0.83"), minimum_sample_size=3)
    assert mapped.used_calibration is True
    assert mapped.band_sample_size == 3
    assert mapped.calibrated_confidence == Decimal("0.666667")


def test_sparse_data_falls_back_to_raw_confidence(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    _seed_outcome(db_session, part_id=part.id, vendor_id=vendor.id, score="0.72", accepted=True, day=1)
    confidence_calibration_service.rebuild_calibration_data(db_session, minimum_sample_size=3)

    mapped = confidence_calibration_service.map_confidence(db_session, raw_confidence=Decimal("0.72"), minimum_sample_size=3)
    assert mapped.used_calibration is False
    assert mapped.fallback_reason == "insufficient_sample_size"
    assert mapped.calibrated_confidence == Decimal("0.720000")


def test_anti_jitter_preserves_prior_rank_for_small_delta():
    current = [
        _candidate(vendor_id="vendor-b", vendor_name="Vendor B", rank=1, score=0.912, line_total=100.0),
        _candidate(vendor_id="vendor-a", vendor_name="Vendor A", rank=2, score=0.900, line_total=101.0),
    ]
    previous_line = {
        "recommended_vendor_id": "vendor-a",
        "candidate_rankings": [
            {"vendor_id": "vendor-a", "rank": 1, "score": 0.905, "estimated_line_total": 100.5, "estimated_freight_total": 10.0, "evidence": {"phase2a": {"availability_evidence": {"feasible": True}}, "anomaly_summary": {"has_high_severity": False}}},
            {"vendor_id": "vendor-b", "rank": 2, "score": 0.901, "estimated_line_total": 100.0, "estimated_freight_total": 10.0, "evidence": {"phase2a": {"availability_evidence": {"feasible": True}}, "anomaly_summary": {"has_high_severity": False}}},
        ],
    }

    decision = recommendation_stability_service.apply(candidate_rankings=current, previous_line=previous_line)

    assert decision.candidate_rankings[0].vendor_id == "vendor-a"
    assert decision.rank_changed is False
    assert decision.material_change_flag is False
    assert decision.stability_reason == "small_score_delta_preserved_prior_rank"


def test_material_change_overrides_stability_rule():
    current = [
        _candidate(vendor_id="vendor-b", vendor_name="Vendor B", rank=1, score=0.912, line_total=100.0),
        _candidate(vendor_id="vendor-a", vendor_name="Vendor A", rank=2, score=0.900, line_total=135.0),
    ]
    previous_line = {
        "recommended_vendor_id": "vendor-a",
        "candidate_rankings": [
            {"vendor_id": "vendor-a", "rank": 1, "score": 0.905, "estimated_line_total": 100.0, "estimated_freight_total": 10.0, "evidence": {"phase2a": {"availability_evidence": {"feasible": True}}, "anomaly_summary": {"has_high_severity": False}}},
            {"vendor_id": "vendor-b", "rank": 2, "score": 0.901, "estimated_line_total": 100.0, "estimated_freight_total": 10.0, "evidence": {"phase2a": {"availability_evidence": {"feasible": True}}, "anomaly_summary": {"has_high_severity": False}}},
        ],
    }

    decision = recommendation_stability_service.apply(candidate_rankings=current, previous_line=previous_line)

    assert decision.candidate_rankings[0].vendor_id == "vendor-b"
    assert decision.rank_changed is True
    assert decision.material_change_flag is True
    assert decision.stability_reason == "material_price_change"