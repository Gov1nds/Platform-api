from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.enrichment import BOMLineEvidenceCoverageFact, EvidenceGapBacklogItem, EnrichmentRunLog
from app.models.project import Project
from app.services.enrichment.evidence_operations_service import evidence_operations_service
from app.services.enrichment.recompute_service import phase2a_recompute_service


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _make_bom(db_session, test_org, *, project_id: str | None = None) -> BOM:
    row = BOM(
        organization_id=test_org.id,
        project_id=project_id,
        source_file_name="batch5.csv",
        status="INGESTED",
        delivery_location="Dallas",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_project(db_session, test_user, test_org, bom: BOM, *, status: str = "SOURCING_ACTIVE") -> Project:
    row = Project(
        bom_id=bom.id,
        user_id=test_user.id,
        organization_id=test_org.id,
        name="Batch 5 Project",
        status=status,
        project_metadata={"origin_country": "CHN", "destination_country": "USA"},
    )
    db_session.add(row)
    db_session.flush()
    bom.project_id = row.id
    db_session.flush()
    return row


def _phase2a_bundle(*, mapped: bool = True, fresh_offer: bool = True, fresh_availability: bool = True, hs6: str | None = "853400", tariff_row: bool = True, lane_band: bool = True, stale: bool = False) -> dict:
    return {
        "phase2a": {
            "offer_evidence": {
                "selected_mapping_id": "map-1" if mapped else None,
                "canonical_sku_id": "canon-1" if mapped else None,
                "freshness_status": "STALE" if stale else ("FRESH" if fresh_offer else "MISSING"),
            },
            "availability_evidence": {
                "snapshot_id": "av-1" if fresh_availability else None,
                "freshness_status": "STALE" if stale else ("FRESH" if fresh_availability else "MISSING"),
                "uncertainty_reason": None if fresh_availability else "availability_unknown",
            },
            "tariff_evidence": {
                "hs_code": hs6,
                "tariff_schedule_id": "tariff-1" if tariff_row else None,
                "coverage_status": "covered" if tariff_row else "out_of_scope",
                "uncertainty_reason": None if tariff_row else "tariff_out_of_scope",
            },
            "freight_evidence": {
                "lane_rate_band_id": "lane-1" if lane_band else None,
                "lane_key": "CHN|SZ|USA|DAL|AIR",
                "coverage_status": "covered" if lane_band else "missing",
                "uncertainty_reason": None if lane_band else "missing_lane_coverage",
            },
            "freshness_summary": {
                "status": "stale" if stale else "fresh",
                "offer_status": "stale" if stale else "fresh",
                "availability_status": "stale" if stale else "fresh",
                "tariff_status": "fresh",
                "freight_status": "fresh",
            },
            "confidence_summary": {"score": 0.88 if mapped else 0.20},
            "uncertainty_flags": {
                "offer_missing": not mapped,
                "availability_missing": not fresh_availability,
                "hs_uncertain": hs6 is None,
                "tariff_uncertain": not tariff_row,
                "freight_uncertain": not lane_band,
                "canonical_offer_conflict": False,
                "canonical_availability_conflict": False,
                "canonical_offer_stale": stale,
                "canonical_availability_stale": stale,
            },
        }
    }


def _make_part(db_session, test_org, bom: BOM, *, row_number: int, procurement_class: str = "electronics", quantity: str = "10", mapped: bool = True, fresh_offer: bool = True, fresh_availability: bool = True, tariff_row: bool = True, lane_band: bool = True, stale: bool = False, strategy_gate: str = "award-ready", candidate_count: int = 1) -> BOMPart:
    row = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="SCORED",
        row_number=row_number,
        description=f"Part {row_number}",
        quantity=Decimal(quantity),
        manufacturer="Acme",
        mpn=f"MPN-{row_number}",
        canonical_part_key=f"part:{row_number}" if mapped else None,
        procurement_class=procurement_class,
        enrichment_json=_phase2a_bundle(
            mapped=mapped,
            fresh_offer=fresh_offer,
            fresh_availability=fresh_availability,
            tariff_row=tariff_row,
            lane_band=lane_band,
            stale=stale,
        ),
        score_cache_json={
            "strategy_gate": strategy_gate,
            "recommended_vendor_id": "vendor-1" if candidate_count else None,
            "candidate_rankings": ([{"vendor_id": "vendor-1", "estimated_line_total": 2500.0}] if candidate_count else []),
            "pricing_context": {"estimated_line_total": 2500.0},
            "evidence_summary": {},
        },
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_coverage_snapshot_generation_groups_counts_by_taxonomy(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org)
    project = _make_project(db_session, test_user, test_org, bom)
    _make_part(db_session, test_org, bom, row_number=1, procurement_class="electronics", strategy_gate="award-ready")
    _make_part(db_session, test_org, bom, row_number=2, procurement_class="electronics", fresh_availability=False, strategy_gate="rfq-first")
    _make_part(db_session, test_org, bom, row_number=3, procurement_class="fastener", mapped=False, tariff_row=False, lane_band=False, strategy_gate="rfq-first", candidate_count=0)

    snapshot_at = _utc(2026, 4, 15, 14, 35)
    facts = evidence_operations_service.snapshot_coverage_facts(
        db_session,
        tenant_id=test_org.id,
        project_id=project.id,
        snapshot_at=snapshot_at,
    )

    assert len(facts) == 2
    electronics = next(row for row in facts if row.taxonomy_code == "electronics")
    assert electronics.lines_total == 2
    assert electronics.lines_with_sku_mapping == 2
    assert electronics.lines_with_fresh_offer == 2
    assert electronics.lines_with_fresh_availability == 1
    assert electronics.lines_award_ready == 1
    assert electronics.lines_rfq_first == 1

    fastener = next(row for row in facts if row.taxonomy_code == "fastener")
    assert fastener.lines_total == 1
    assert fastener.lines_with_sku_mapping == 0
    assert fastener.lines_with_tariff_row == 0
    assert fastener.lines_with_lane_band == 0

    persisted = db_session.query(BOMLineEvidenceCoverageFact).all()
    assert len(persisted) == 2
    assert all(row.snapshot_date == _utc(2026, 4, 15) for row in persisted)



def test_missing_evidence_classification_and_backlog_dedup(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org)
    project = _make_project(db_session, test_user, test_org, bom)
    part = _make_part(
        db_session,
        test_org,
        bom,
        row_number=1,
        mapped=False,
        fresh_availability=False,
        tariff_row=False,
        lane_band=False,
        stale=True,
        strategy_gate="rfq-first",
        candidate_count=0,
    )

    recommendation = dict(part.score_cache_json)
    first = evidence_operations_service.route_backlog_for_line(
        db_session,
        bom_part=part,
        project=project,
        recommendation=recommendation,
        observed_at=_utc(2026, 4, 15, 10, 0),
    )
    assert {row.category for row in first} == {
        "missing_mapping",
        "missing_availability",
        "tariff_out_of_scope",
        "lane_missing",
        "weak_vendor_evidence",
        "stale_critical_signal",
    }
    assert sorted(part.score_cache_json["evidence_summary"]["missing_critical_evidence_categories"]) == [
        "lane_missing",
        "missing_availability",
        "missing_mapping",
        "stale_critical_signal",
        "tariff_out_of_scope",
        "weak_vendor_evidence",
    ]
    assert part.score_cache_json["evidence_summary"]["evidence_completeness_score"] == 0.0

    second = evidence_operations_service.route_backlog_for_line(
        db_session,
        bom_part=part,
        project=project,
        recommendation=part.score_cache_json,
        observed_at=_utc(2026, 4, 15, 11, 0),
    )
    assert len(second) == 6

    rows = db_session.query(EvidenceGapBacklogItem).filter(EvidenceGapBacklogItem.bom_part_id == part.id).all()
    assert len(rows) == 6
    assert all(row.request_count == 2 for row in rows)
    assert all(row.status == "open" for row in rows)



def test_quota_and_scope_coalescing_controls_bound_refresh_burst(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part1 = _make_part(db_session, test_org, bom, row_number=1, procurement_class="electronics")
    part2 = _make_part(db_session, test_org, bom, row_number=2, procurement_class="electronics")

    from app.workers import pipeline as worker_pipeline

    dispatched: list[dict] = []

    def _fake_apply_async(*, kwargs, countdown):
        dispatched.append({"kwargs": kwargs, "countdown": countdown})
        return {"ok": True}

    original_quota = phase2a_recompute_service.TENANT_MAX_RECOMPUTES_PER_MINUTE
    phase2a_recompute_service.TENANT_MAX_RECOMPUTES_PER_MINUTE = 1
    original_task = worker_pipeline.task_recompute_bom_line_phase2a.apply_async
    worker_pipeline.task_recompute_bom_line_phase2a.apply_async = _fake_apply_async
    try:
        result = phase2a_recompute_service.enqueue_recompute_for_bom_lines(
            db_session,
            bom_line_ids=[part1.id, part2.id],
            reason="burst_refresh",
            dataset="sku_offers",
        )
    finally:
        phase2a_recompute_service.TENANT_MAX_RECOMPUTES_PER_MINUTE = original_quota
        worker_pipeline.task_recompute_bom_line_phase2a.apply_async = original_task

    assert result["enqueued"] == 1
    assert result["throttled_by_tenant"] == 1
    assert len(dispatched) == 1

    scope_logs = db_session.query(EnrichmentRunLog).filter(EnrichmentRunLog.run_scope == "coalesce_scope").all()
    assert any((row.source_metadata or {}).get("scope_type") == "canonical_sku" for row in scope_logs)
    canonical_scope_values = {(row.source_metadata or {}).get("scope_value") for row in scope_logs if (row.source_metadata or {}).get("scope_type") == "canonical_sku"}
    assert canonical_scope_values == {"part:1", "part:2"} or canonical_scope_values == {"part:1", "part:2"}



def test_active_project_prioritization_orders_refresh_queue(db_session, test_org, test_user):
    active_bom = _make_bom(db_session, test_org)
    active_project = _make_project(db_session, test_user, test_org, active_bom, status="SOURCING_ACTIVE")
    active_part = _make_part(db_session, test_org, active_bom, row_number=1, quantity="5000", strategy_gate="rfq-first", candidate_count=0)

    inactive_bom = _make_bom(db_session, test_org)
    inactive_project = _make_project(db_session, test_user, test_org, inactive_bom, status="CLOSED")
    inactive_part = _make_part(db_session, test_org, inactive_bom, row_number=2, quantity="1", strategy_gate="award-ready")

    active_priority = evidence_operations_service.priority_score_for_line(
        db_session,
        bom_part=active_part,
        project=active_project,
        categories=["missing_mapping", "stale_critical_signal"],
    )
    inactive_priority = evidence_operations_service.priority_score_for_line(
        db_session,
        bom_part=inactive_part,
        project=inactive_project,
        categories=[],
    )
    assert active_priority > inactive_priority

    ordered = phase2a_recompute_service._prioritize_bom_line_ids(db_session, bom_line_ids=[inactive_part.id, active_part.id])
    assert ordered[0] == active_part.id