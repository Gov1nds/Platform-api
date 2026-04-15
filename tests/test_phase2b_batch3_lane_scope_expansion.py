from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.enrichment import LaneRateBand, LaneScopeRegistry
from app.models.project import Project
from app.schemas.enrichment import LaneLookupContextDTO
from app.services.enrichment.lane_rate_band_lookup_service import lane_rate_band_lookup_service
from app.services.enrichment.lane_rate_ingestion_service import lane_rate_ingestion_service
from app.services.enrichment.lane_scope_service import lane_scope_service


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_bom(db_session, test_org, delivery_location: str = "TX") -> BOM:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="phase2b-batch3.csv",
        status="INGESTED",
        delivery_location=delivery_location,
    )
    db_session.add(bom)
    db_session.flush()
    return bom


def _make_bom_part(db_session, test_org, bom: BOM, *, weight_kg: str | None = None) -> BOMPart:
    specs = {"weight_kg": weight_kg} if weight_kg is not None else {}
    row = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Batch 3 lane part",
        quantity=Decimal("10"),
        manufacturer="Acme",
        mpn="LANE-B3",
        canonical_part_key="lane:batch3",
        normalization_trace_json={"normalized_mpn": "LANE-B3"},
        specs=specs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_project(db_session, test_user, test_org, bom: BOM, **metadata) -> Project:
    row = Project(
        bom_id=bom.id,
        user_id=test_user.id,
        organization_id=test_org.id,
        name="Batch 3 Lane Project",
        status="DRAFT",
        project_metadata=metadata,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_lane_ingestion_preserves_history_and_updates_scope_registry(db_session, test_org):
    _make_bom_part(db_session, test_org, _make_bom(db_session, test_org))

    first = lane_rate_ingestion_service.ingest_lane_rate_band(
        db_session,
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        mode="air",
        service_level="priority",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("100"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("6.00"),
        effective_from=_utc(2025, 1, 1),
        source_system="seed",
        source_record_id="lane-b3-1",
        fetched_at=_utc(2026, 4, 10),
        priority_tier="high",
        refresh_cadence="weekly",
    )
    second = lane_rate_ingestion_service.ingest_lane_rate_band(
        db_session,
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        mode="air",
        service_level="priority",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("100"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("7.50"),
        effective_from=_utc(2026, 1, 1),
        source_system="seed",
        source_record_id="lane-b3-2",
        fetched_at=_utc(2026, 4, 12),
        priority_tier="critical",
        refresh_cadence="daily",
    )
    db_session.commit()

    rows = (
        db_session.query(LaneRateBand)
        .filter(
            LaneRateBand.origin_country == "CHN",
            LaneRateBand.destination_country == "USA",
            LaneRateBand.mode == "air",
            LaneRateBand.service_level == "priority",
        )
        .order_by(LaneRateBand.effective_from.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].id == first.id
    assert rows[0].effective_to == _utc(2026, 1, 1)
    assert rows[1].id == second.id
    assert rows[1].effective_to is None

    scope = db_session.query(LaneScopeRegistry).filter(LaneScopeRegistry.origin_country == "CHN").one()
    assert scope.scope_status == "covered"
    assert scope.priority_tier == "critical"
    assert scope.refresh_cadence == "daily"
    assert scope.last_refreshed_at == _utc(2026, 4, 12)


def test_lane_lookup_prefers_service_level_specific_effective_row(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org, delivery_location="Dallas")
    part = _make_bom_part(db_session, test_org, bom)
    project = _make_project(
        db_session,
        test_user,
        test_org,
        bom,
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        shipping_mode="air",
        service_level="priority",
    )

    lane_rate_ingestion_service.ingest_lane_rate_band(
        db_session,
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        mode="air",
        service_level=None,
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("999"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("9.00"),
        min_charge=Decimal("25"),
        effective_from=_utc(2025, 1, 1),
        source_system="seed",
        source_record_id="lane-generic",
        fetched_at=_utc(2026, 4, 10),
    )
    specific = lane_rate_ingestion_service.ingest_lane_rate_band(
        db_session,
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        mode="air",
        service_level="priority",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("999"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("6.25"),
        min_charge=Decimal("25"),
        effective_from=_utc(2025, 1, 1),
        source_system="seed",
        source_record_id="lane-priority",
        fetched_at=_utc(2026, 4, 10),
    )
    db_session.flush()

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        project=project,
        bom=bom,
        bom_part=part,
        context=LaneLookupContextDTO(weight_kg=Decimal("20")),
        lookup_date=_utc(2026, 4, 14),
    )

    assert result.resolved is True
    assert result.coverage_status == "in_scope"
    assert result.lane_rate_band_id == specific.id
    assert result.service_level == "priority"
    assert result.p50_freight_estimate == Decimal("125.00")
    assert result.refresh_cadence in {"weekly", "daily"}


def test_lane_lookup_surfaces_out_of_scope_then_missing_coverage_explicitly(db_session, test_org):
    bom = _make_bom(db_session, test_org, delivery_location="Chicago")
    part = _make_bom_part(db_session, test_org, bom)
    ctx = LaneLookupContextDTO(
        origin_country="MYS",
        origin_region="Penang",
        destination_country="USA",
        destination_region="Chicago",
        mode="air",
        service_level="standard",
        weight_kg=Decimal("5"),
    )

    first = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        bom=bom,
        bom_part=part,
        context=ctx,
        lookup_date=_utc(2026, 4, 14),
    )
    assert first.resolved is False
    assert first.coverage_status == "out_of_scope"
    assert first.uncertainty_reason == "lane_out_of_scope"

    row = lane_scope_service.register_lane_activity(
        db_session,
        context=ctx,
        source="ops",
        source_metadata={"scope_status": "covered", "priority_tier": "high", "refresh_cadence": "weekly"},
        touched_at=_utc(2026, 4, 14),
    )
    assert row is not None

    second = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        bom=bom,
        bom_part=part,
        context=ctx,
        lookup_date=_utc(2026, 4, 14),
    )
    assert second.resolved is False
    assert second.coverage_status == "missing"
    assert second.uncertainty_reason == "missing_lane_coverage"
    assert second.priority_tier == "high"


def test_lane_refresh_candidates_prioritize_active_high_value_recent_usage(db_session):
    now = datetime(2026, 4, 15, tzinfo=timezone.utc)
    critical = lane_scope_service.register_lane_activity(
        db_session,
        context=LaneLookupContextDTO(
            origin_country="CHN",
            destination_country="USA",
            mode="air",
            service_level="priority",
            weight_kg=Decimal("500"),
        ),
        source="platform-api",
        source_metadata={"scope_status": "covered", "priority_tier": "critical", "refresh_cadence": "daily"},
        touched_at=now - timedelta(hours=1),
    )
    standard = lane_scope_service.register_lane_activity(
        db_session,
        context=LaneLookupContextDTO(
            origin_country="THA",
            destination_country="USA",
            mode="sea",
            service_level="standard",
            weight_kg=Decimal("20"),
        ),
        source="platform-api",
        source_metadata={"scope_status": "covered", "priority_tier": "standard", "refresh_cadence": "weekly"},
        touched_at=now - timedelta(days=1),
    )
    low = lane_scope_service.register_lane_activity(
        db_session,
        context=LaneLookupContextDTO(
            origin_country="VNM",
            destination_country="USA",
            mode="sea",
            service_level="economy",
            weight_kg=Decimal("2"),
        ),
        source="platform-api",
        source_metadata={"scope_status": "covered", "priority_tier": "low", "refresh_cadence": "monthly"},
        touched_at=now - timedelta(days=10),
    )
    assert critical and standard and low

    critical.last_refreshed_at = now - timedelta(days=2)
    standard.last_refreshed_at = now - timedelta(days=8)
    low.last_refreshed_at = now - timedelta(days=40)
    db_session.flush()

    rows = lane_scope_service.list_refresh_candidates(db_session, limit=3, now=now)
    assert [row.lane_key for row in rows] == [critical.lane_key, standard.lane_key, low.lane_key]