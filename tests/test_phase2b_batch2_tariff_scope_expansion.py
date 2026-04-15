from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.market import TariffSchedule, TariffScopeRegistry
from app.schemas.enrichment import HSResolutionDTO
from app.services.enrichment.tariff_ingestion_service import tariff_ingestion_service
from app.services.enrichment.tariff_lookup_service import tariff_lookup_service


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_bom_part(db_session, test_org, **overrides) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch2-tariff.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Tariff scope expansion part",
        quantity=Decimal("10"),
        manufacturer="Acme",
        mpn="MPN-2B",
        canonical_part_key="part:acme:mpn-2b",
        category_code="electronics",
        material="copper",
        normalization_trace_json={"normalized_mpn": "MPN-2B"},
        **overrides,
    )
    db_session.add(part)
    db_session.flush()
    return part


def _resolved_hs(part: BOMPart, hs_code: str = "8534009010", hs_version: str = "2022") -> HSResolutionDTO:
    return HSResolutionDTO(
        bom_part_id=part.id,
        resolution_status="resolved",
        resolved=True,
        hs_code=hs_code,
        hs_version=hs_version,
        jurisdiction="USA",
        confidence=Decimal("0.95"),
        mapping_method="manual",
        review_status="APPROVED",
        matched_on="bom_part_id",
        source_system="seed",
        source_record_id="hs-b2-1",
        source_metadata={},
        uncertainty_reason=None,
        mapping_id="map-b2-1",
    )


def test_tariff_lookup_supports_multiple_jurisdictions_with_scope_registry(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part)

    tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="USA",
        origin_country="CHN",
        hs_code="853400",
        duty_rate_pct=Decimal("12.5000"),
        additional_taxes_pct=Decimal("1.5000"),
        effective_from=_utc(2025, 1, 1),
        source="usitc",
        confidence=Decimal("0.92"),
        coverage_level="full",
        update_cadence="weekly",
        fetched_at=_utc(2026, 4, 10),
        source_record_id="usa-853400-2025-01-01",
    )
    tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="IND",
        origin_country="CHN",
        hs_code="853400",
        duty_rate_pct=Decimal("7.0000"),
        additional_taxes_pct=Decimal("0.0000"),
        effective_from=_utc(2025, 1, 1),
        source="cbic",
        confidence=Decimal("0.89"),
        coverage_level="partial",
        update_cadence="monthly",
        fetched_at=_utc(2026, 4, 11),
        source_record_id="ind-853400-2025-01-01",
    )
    db_session.commit()

    us_result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2026, 4, 12),
        bom_part=part,
    )
    in_result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="IND",
        origin_country="CHN",
        lookup_date=_utc(2026, 4, 12),
        bom_part=part,
    )

    assert us_result.resolved is True
    assert us_result.coverage_status == "in_scope"
    assert us_result.coverage_level == "full"
    assert us_result.total_tariff_rate_pct == Decimal("14.0000")

    assert in_result.resolved is True
    assert in_result.coverage_status == "in_scope"
    assert in_result.coverage_level == "partial"
    assert in_result.total_tariff_rate_pct == Decimal("7.0000")

    scope_rows = db_session.query(TariffScopeRegistry).all()
    assert {row.import_country for row in scope_rows} == {"USA", "IND"}


def test_tariff_lookup_prefers_effective_row_and_national_extension_when_available(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part, hs_code="8534009010", hs_version="2022")

    tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="USA",
        origin_country="CHN",
        hs_code="853400",
        duty_rate_pct=Decimal("5.0000"),
        additional_taxes_pct=Decimal("0.0000"),
        effective_from=_utc(2025, 1, 1),
        source="general",
        confidence=Decimal("0.80"),
        coverage_level="full",
        fetched_at=_utc(2026, 4, 10),
        source_record_id="general-hs6",
    )
    tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="USA",
        origin_country="CHN",
        hs_code="8534009010",
        national_extension_code="8534009010",
        tariff_code_type="national_extension",
        duty_rate_pct=Decimal("8.5000"),
        additional_taxes_pct=Decimal("1.5000"),
        effective_from=_utc(2026, 1, 1),
        source="extension-current",
        confidence=Decimal("0.96"),
        hs_version="2022",
        coverage_level="full",
        fetched_at=_utc(2026, 4, 10),
        source_record_id="us-ext-current",
    )
    db_session.commit()

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="USA",
        origin_country="CHN",
        lookup_date=_utc(2026, 4, 12),
        customs_value=Decimal("1000"),
        bom_part=part,
    )

    assert result.resolved is True
    assert result.national_extension_code == "8534009010"
    assert result.tariff_code_type == "national_extension"
    assert result.source_metadata["matched_on_tariff_code"] in {
        "national_extension_exact",
        "national_extension_from_hs_code",
    }
    assert result.total_tariff_rate_pct == Decimal("10.0000")
    assert result.estimated_total_tariff == Decimal("100.0000")


def test_tariff_lookup_returns_out_of_scope_status_explicitly(db_session, test_org):
    part = _make_bom_part(db_session, test_org)
    hs_resolution = _resolved_hs(part, hs_code="84733000")

    result = tariff_lookup_service.lookup_for_hs_resolution(
        db_session,
        hs_resolution=hs_resolution,
        destination_country="BRA",
        origin_country="CHN",
        lookup_date=_utc(2026, 4, 12),
        bom_part=part,
    )

    assert result.resolved is False
    assert result.coverage_status == "out_of_scope"
    assert result.uncertainty_reason == "tariff_jurisdiction_out_of_scope"
    assert result.tariff_schedule_id is None


def test_tariff_ingestion_preserves_history_by_closing_prior_window_non_destructively(db_session, test_org):
    _make_bom_part(db_session, test_org)

    first = tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="USA",
        origin_country="CHN",
        hs_code="853400",
        duty_rate_pct=Decimal("4.0000"),
        effective_from=_utc(2025, 1, 1),
        source="usitc",
        coverage_level="full",
        fetched_at=_utc(2026, 4, 1),
        source_record_id="hist-1",
    )
    second = tariff_ingestion_service.ingest_schedule(
        db_session,
        destination_country="USA",
        origin_country="CHN",
        hs_code="853400",
        duty_rate_pct=Decimal("6.0000"),
        effective_from=_utc(2026, 1, 1),
        source="usitc",
        coverage_level="full",
        fetched_at=_utc(2026, 4, 10),
        source_record_id="hist-2",
    )
    db_session.commit()

    rows = (
        db_session.query(TariffSchedule)
        .filter(
            TariffSchedule.destination_country == "USA",
            TariffSchedule.origin_country == "CHN",
            TariffSchedule.hs6 == "853400",
        )
        .order_by(TariffSchedule.effective_from.asc())
        .all()
    )

    assert len(rows) == 2
    assert rows[0].id == first.id
    assert rows[0].effective_to == _utc(2026, 1, 1)
    assert rows[1].id == second.id
    assert rows[1].effective_to is None
