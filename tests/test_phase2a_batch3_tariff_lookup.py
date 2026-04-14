from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.market import TariffSchedule
from app.schemas.enrichment import HSResolutionDTO
from app.services.enrichment.tariff_lookup_service import tariff_lookup_service


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_bom_part(db_session, test_org, **overrides) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch3-part2.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Tariff test part",
        quantity=Decimal("10"),
        manufacturer="Acme",
        mpn="MPN-1",
        canonical_part_key="part:acme:mpn-1",
        category_code="electronics",
        material="copper",
        normalization_trace_json={"normalized_mpn": "MPN-1"},
        **overrides,
    )
    db_session.add(part)
    db_session.flush()
    return part


def _resolved_hs(part: BOMPart, hs_code: str = "85340090") -> HSResolutionDTO:
    return HSResolutionDTO(
        bom_part_id=part.id,
        resolution_status="resolved",
        resolved=True,
        hs_code=hs_code,
        hs_version="2022",
        jurisdiction="USA",
        confidence=Decimal("0.95"),
        mapping_method="manual",
        review_status="APPROVED",
        matched_on="bom_part_id",
        source_system="seed",
        source_record_id="hs-1",
        source_metadata={},
        uncertainty_reason=None,
        mapping_id="map-1",
    )


def test_tariff_lookup_prefers_origin_specific_valid_row(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part)

    db_session.add(
        TariffSchedule(
            hs_code="853400",
            origin_country="MFN",
            destination_country="USA",
            duty_rate_pct=Decimal("4.0000"),
            additional_taxes_pct=Decimal("0.5000"),
            source="seed-general",
            confidence=Decimal("0.70"),
            effective_from=_utc(2025, 1, 1),
            effective_to=None,
        )
    )
    specific = TariffSchedule(
        hs_code="853400",
        origin_country="CHN",
        destination_country="USA",
        duty_rate_pct=Decimal("12.5000"),
        additional_taxes_pct=Decimal("3.0000"),
        source="seed-specific",
        confidence=Decimal("0.93"),
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
    )
    db_session.add(specific)
    db_session.flush()

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2026, 4, 1),
        customs_value=Decimal("1000"),
        bom_part=part,
        trace_id="trace-tariff-1",
    )

    assert result.resolved is True
    assert result.lookup_status == "resolved"
    assert result.tariff_schedule_id == specific.id
    assert result.duty_rate_pct == Decimal("12.5000")
    assert result.additional_taxes_pct == Decimal("3.0000")
    assert result.total_tariff_rate_pct == Decimal("15.5000")
    assert result.estimated_duty == Decimal("125.0000")
    assert result.estimated_additional_taxes == Decimal("30.0000")
    assert result.estimated_total_tariff == Decimal("155.0000")
    assert result.source_metadata["matched_on_origin"] == "origin_country_exact"


def test_tariff_lookup_falls_back_to_general_origin_when_specific_missing(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part)

    general = TariffSchedule(
        hs_code="853400",
        origin_country="ALL",
        destination_country="USA",
        duty_rate_pct=Decimal("5.0000"),
        additional_taxes_pct=Decimal("1.0000"),
        source="seed-general",
        confidence=Decimal("0.81"),
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
    )
    db_session.add(general)
    db_session.flush()

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="MYS",
        lookup_date=_utc(2026, 2, 15),
        bom_part=part,
    )

    assert result.resolved is True
    assert result.lookup_status == "resolved"
    assert result.tariff_schedule_id == general.id
    assert result.duty_rate_pct == Decimal("5.0000")
    assert result.total_tariff_rate_pct == Decimal("6.0000")
    assert result.source_metadata["matched_on_origin"] == "origin_country_general"


def test_tariff_lookup_uses_effective_date_window(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part)

    expired = TariffSchedule(
        hs_code="853400",
        origin_country="CHN",
        destination_country="USA",
        duty_rate_pct=Decimal("8.0000"),
        additional_taxes_pct=Decimal("1.0000"),
        source="seed-old",
        confidence=Decimal("0.88"),
        effective_from=_utc(2024, 1, 1),
        effective_to=_utc(2024, 12, 31),
    )
    current = TariffSchedule(
        hs_code="853400",
        origin_country="CHN",
        destination_country="USA",
        duty_rate_pct=Decimal("10.0000"),
        additional_taxes_pct=Decimal("2.0000"),
        source="seed-current",
        confidence=Decimal("0.90"),
        effective_from=_utc(2025, 1, 1),
        effective_to=None,
    )
    db_session.add(expired)
    db_session.add(current)
    db_session.flush()

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2025, 5, 1),
        bom_part=part,
    )

    assert result.resolved is True
    assert result.tariff_schedule_id == current.id
    assert result.duty_rate_pct == Decimal("10.0000")
    assert result.additional_taxes_pct == Decimal("2.0000")


def test_tariff_lookup_returns_explicit_uncertainty_when_hs_is_low_confidence(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = HSResolutionDTO(
        bom_part_id=part.id,
        resolution_status="needs_review",
        resolved=False,
        hs_code="85340090",
        hs_version="2022",
        jurisdiction="USA",
        confidence=Decimal("0.42"),
        mapping_method="seed",
        review_status="AUTO",
        matched_on="canonical_part_key",
        source_system="seed",
        source_record_id="low-1",
        source_metadata={},
        uncertainty_reason="low_confidence_mapping",
        mapping_id="map-low-1",
    )

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2026, 1, 1),
        bom_part=part,
    )

    assert result.resolved is False
    assert result.lookup_status == "uncertain"
    assert result.uncertainty_reason == "low_confidence_mapping"
    assert result.tariff_schedule_id is None


def test_tariff_lookup_returns_explicit_uncertainty_when_schedule_missing(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part, hs_code="84733000")

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2026, 3, 1),
        bom_part=part,
    )

    assert result.resolved is False
    assert result.lookup_status == "uncertain"
    assert result.uncertainty_reason == "no_tariff_schedule_found"
    assert result.hs6 == "847330"