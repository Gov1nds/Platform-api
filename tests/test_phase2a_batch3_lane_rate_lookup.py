from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.enrichment import LaneRateBand
from app.models.project import Project
from app.schemas.enrichment import LaneLookupContextDTO
from app.services.enrichment.lane_rate_band_lookup_service import (
    lane_rate_band_lookup_service,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_bom(db_session, test_org, delivery_location: str = "TX") -> BOM:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch3-part3.csv",
        status="INGESTED",
        delivery_location=delivery_location,
    )
    db_session.add(bom)
    db_session.flush()
    return bom


def _make_bom_part(db_session, test_org, bom: BOM) -> BOMPart:
    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Lane test part",
        quantity=Decimal("10"),
        manufacturer="Acme",
        mpn="LANE-1",
        canonical_part_key="lane:acme:1",
        normalization_trace_json={"normalized_mpn": "LANE-1"},
    )
    db_session.add(part)
    db_session.flush()
    return part


def _make_project(db_session, test_user, test_org, bom: BOM, **metadata) -> Project:
    project = Project(
        bom_id=bom.id,
        user_id=test_user.id,
        organization_id=test_org.id,
        name="Lane Project",
        status="DRAFT",
        project_metadata=metadata,
    )
    db_session.add(project)
    db_session.flush()
    return project


def test_lane_lookup_prefers_region_and_weight_specific_valid_band(db_session, test_org, test_user):
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
    )

    broad = LaneRateBand(
        origin_country="CHN",
        origin_region=None,
        destination_country="USA",
        destination_region=None,
        mode="air",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("9999"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("8.00"),
        min_charge=Decimal("50"),
        transit_days_min=5,
        transit_days_max=8,
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
        source_system="seed",
        source_record_hash="lane-broad-1",
        source_metadata={},
    )
    specific = LaneRateBand(
        origin_country="CHN",
        origin_region="Shenzhen",
        destination_country="USA",
        destination_region="Dallas",
        mode="air",
        min_weight_kg=Decimal("10"),
        max_weight_kg=Decimal("100"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("6.50"),
        min_charge=Decimal("40"),
        transit_days_min=3,
        transit_days_max=5,
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
        source_system="seed",
        source_record_hash="lane-specific-1",
        source_metadata={},
    )
    db_session.add(broad)
    db_session.add(specific)
    db_session.flush()

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        project=project,
        bom=bom,
        bom_part=part,
        context=LaneLookupContextDTO(weight_kg=Decimal("20")),
        lookup_date=_utc(2026, 4, 1),
        trace_id="trace-lane-1",
    )

    assert result.resolved is True
    assert result.lookup_status == "resolved"
    assert result.lane_rate_band_id == specific.id
    assert result.currency == "USD"
    assert result.p50_freight_estimate == Decimal("130.00")
    assert result.p90_freight_estimate == Decimal("130.00")
    assert result.transit_days_min == 3
    assert result.transit_days_max == 5
    assert result.source_metadata["p90_estimate_method"] == "single_band_proxy"


def test_lane_lookup_uses_effective_date_window(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org, delivery_location="TX")
    part = _make_bom_part(db_session, test_org, bom)
    project = _make_project(
        db_session,
        test_user,
        test_org,
        bom,
        origin_country="CHN",
        destination_country="USA",
        shipping_mode="sea",
    )

    expired = LaneRateBand(
        origin_country="CHN",
        destination_country="USA",
        mode="sea",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("9999"),
        currency="USD",
        rate_type="flat",
        rate_value=Decimal("500"),
        transit_days_min=20,
        transit_days_max=25,
        effective_from=_utc(2024, 1, 1),
        effective_to=_utc(2024, 12, 31),
        source_system="seed",
        source_record_hash="lane-expired-1",
        source_metadata={},
    )
    current = LaneRateBand(
        origin_country="CHN",
        destination_country="USA",
        mode="sea",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("9999"),
        currency="USD",
        rate_type="flat",
        rate_value=Decimal("650"),
        transit_days_min=18,
        transit_days_max=22,
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
        source_system="seed",
        source_record_hash="lane-current-1",
        source_metadata={},
    )
    db_session.add(expired)
    db_session.add(current)
    db_session.flush()

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        project=project,
        bom=bom,
        bom_part=part,
        lookup_date=_utc(2025, 7, 1),
    )

    assert result.resolved is True
    assert result.lane_rate_band_id == current.id
    assert result.p50_freight_estimate == Decimal("650")
    assert result.transit_days_min == 18
    assert result.transit_days_max == 22


def test_lane_lookup_falls_back_to_country_mode_when_regions_missing(db_session, test_org):
    bom = _make_bom(db_session, test_org, delivery_location="Chicago")
    part = _make_bom_part(db_session, test_org, bom)

    row = LaneRateBand(
        origin_country="MYS",
        destination_country="USA",
        mode="air",
        min_weight_kg=Decimal("0"),
        max_weight_kg=Decimal("9999"),
        currency="USD",
        rate_type="per_kg",
        rate_value=Decimal("9"),
        min_charge=Decimal("25"),
        transit_days_min=4,
        transit_days_max=7,
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
        source_system="seed",
        source_record_hash="lane-country-1",
        source_metadata={},
    )
    db_session.add(row)
    db_session.flush()

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        bom=bom,
        bom_part=part,
        context=LaneLookupContextDTO(
            origin_country="MYS",
            destination_country="USA",
            mode="air",
            destination_region="Chicago",
            weight_kg=Decimal("2"),
        ),
        lookup_date=_utc(2026, 1, 1),
    )

    assert result.resolved is True
    assert result.lane_rate_band_id == row.id
    assert result.p50_freight_estimate == Decimal("25")
    assert result.destination_region is None


def test_lane_lookup_returns_explicit_uncertainty_when_context_incomplete(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_bom_part(db_session, test_org, bom)

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        bom=bom,
        bom_part=part,
        context=LaneLookupContextDTO(
            origin_country=None,
            destination_country="USA",
            mode="sea",
        ),
        lookup_date=_utc(2026, 1, 1),
    )

    assert result.resolved is False
    assert result.lookup_status == "uncertain"
    assert result.uncertainty_reason == "lane_context_incomplete"


def test_lane_lookup_returns_explicit_uncertainty_when_no_band_found(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_bom_part(db_session, test_org, bom)

    result = lane_rate_band_lookup_service.lookup_lane_rate(
        db_session,
        bom=bom,
        bom_part=part,
        context=LaneLookupContextDTO(
            origin_country="CHN",
            destination_country="USA",
            mode="sea",
            weight_kg=Decimal("50"),
        ),
        lookup_date=_utc(2026, 1, 1),
    )

    assert result.resolved is False
    assert result.lookup_status == "uncertain"
    assert result.uncertainty_reason == "no_lane_rate_band_found"