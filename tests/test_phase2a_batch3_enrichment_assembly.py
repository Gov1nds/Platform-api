from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.enrichment import (
    HSMapping,
    LaneRateBand,
    PartToSkuMapping,
    SKUAvailabilitySnapshot,
    SKUOffer,
    SKUOfferPriceBreak,
)
from app.models.market import TariffSchedule
from app.models.project import Project
from app.services.enrichment.phase2a_evidence_service import phase2a_evidence_service


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_project_bundle(db_session, test_user, test_org):
    bom = BOM(
        uploaded_by_user_id=test_user.id,
        organization_id=test_org.id,
        source_file_name="phase2a-part4.csv",
        status="INGESTED",
        target_currency="USD",
        delivery_location="Dallas",
    )
    db_session.add(bom)
    db_session.flush()

    project = Project(
        bom_id=bom.id,
        user_id=test_user.id,
        organization_id=test_org.id,
        name="Phase2A Part4",
        status="DRAFT",
        project_metadata={
            "destination_country": "USA",
            "shipping_mode": "air",
            "service_level": "standard",
        },
    )
    db_session.add(project)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="MCU",
        quantity=Decimal("120"),
        manufacturer="Acme",
        mpn="MCU-1",
        canonical_part_key="mcu:acme:mcu-1",
        category_code="electronics",
        material="copper",
        specs={"weight_kg": "12"},
        enrichment_json={"phase1": {"legacy": True}},
        normalization_trace_json={"normalized_mpn": "MCU-1"},
    )
    db_session.add(part)
    db_session.flush()
    return bom, project, part


def test_phase2a_evidence_assembly_builds_full_bundle(db_session, test_user, test_org):
    bom, project, part = _make_project_bundle(db_session, test_user, test_org)

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="MCU-1",
        normalized_mpn="MCU-1",
        vendor_sku="SKU-123",
        match_method="connector_exact",
        confidence=Decimal("0.93"),
        is_preferred=True,
        source_system="fake",
        source_record_id="prod-1",
        source_record_hash="prod-hash-1",
        source_metadata={"mapping_status": "resolved", "match_score": "0.93"},
    )
    db_session.add(mapping)
    db_session.flush()

    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        offer_name="Standard Reel",
        offer_status="ACTIVE",
        currency="USD",
        uom="ea",
        moq=Decimal("10"),
        spq=Decimal("5"),
        lead_time_days=Decimal("7"),
        country_of_origin="CHN",
        factory_region="Shenzhen",
        freshness_status="FRESH",
        observed_at=_utc(2026, 1, 1),
        valid_from=_utc(2026, 1, 1),
        valid_to=None,
        source_system="fake",
        source_record_id="offer-1",
        source_record_hash="offer-hash-1",
        source_metadata={"ttl_seconds": 1800},
    )
    db_session.add(offer)
    db_session.flush()

    db_session.add_all(
        [
            SKUOfferPriceBreak(
                sku_offer_id=offer.id,
                break_qty=Decimal("1"),
                unit_price=Decimal("2.50"),
                currency="USD",
                price_type="unit",
                valid_from=_utc(2026, 1, 1),
                source_record_hash="break-hash-1",
                source_metadata={},
            ),
            SKUOfferPriceBreak(
                sku_offer_id=offer.id,
                break_qty=Decimal("100"),
                unit_price=Decimal("1.90"),
                currency="USD",
                price_type="unit",
                valid_from=_utc(2026, 1, 1),
                source_record_hash="break-hash-2",
                source_metadata={},
            ),
        ]
    )

    db_session.add(
        SKUAvailabilitySnapshot(
            sku_offer_id=offer.id,
            availability_status="IN_STOCK",
            available_qty=Decimal("250"),
            on_order_qty=Decimal("500"),
            inventory_location="US-WH",
            factory_lead_time_days=Decimal("5"),
            freshness_status="FRESH",
            snapshot_at=_utc(2026, 1, 2),
            source_system="fake",
            source_record_id="avail-1",
            source_record_hash="avail-hash-1",
            source_metadata={"feasibility_tag": "feasible_now", "ttl_seconds": 600},
        )
    )

    db_session.add(
        HSMapping(
            bom_part_id=part.id,
            canonical_part_key=part.canonical_part_key,
            category_code=part.category_code,
            material=part.material,
            hs_code="85340090",
            hs_version="2022",
            jurisdiction="USA",
            mapping_method="manual",
            confidence=Decimal("0.97"),
            review_status="APPROVED",
            source_system="seed",
            source_record_id="hs-1",
            source_record_hash="hs-hash-1",
            source_metadata={},
        )
    )

    db_session.add(
        TariffSchedule(
            hs_code="853400",
            origin_country="CHN",
            destination_country="USA",
            duty_rate_pct=Decimal("12.5000"),
            additional_taxes_pct=Decimal("3.0000"),
            source="seed-specific",
            confidence=Decimal("0.93"),
            effective_from=_utc(2025, 1, 1),
            effective_to=None,
            freshness_status="FRESH",
        )
    )

    db_session.add(
        LaneRateBand(
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
            freshness_status="FRESH",
            effective_from=_utc(2025, 1, 1),
            effective_to=None,
            source_system="seed",
            source_record_hash="lane-hash-1",
            source_metadata={},
        )
    )
    db_session.flush()

    bundle = phase2a_evidence_service.assemble_for_bom_part(
        db_session,
        bom_part=part,
        bom=bom,
        project=project,
        target_currency="USD",
        lookup_date=_utc(2026, 4, 1),
        trace_id="trace-part4-1",
    )

    assert bundle.offer_evidence["selected_offer_id"] == offer.id
    assert bundle.offer_evidence["selected_price_break"]["break_qty"] == "100"
    assert bundle.offer_evidence["selected_price_break"]["unit_price"] == "1.90"
    assert bundle.availability_evidence["feasible"] is True
    assert bundle.tariff_evidence["hs_code"] == "85340090"
    assert bundle.tariff_evidence["estimated_total_tariff"] == "35.340000"
    assert bundle.freight_evidence["p50_freight_estimate"] == "78.00"
    assert bundle.freight_evidence["transit_days_min"] == 3
    assert bundle.uncertainty_flags["offer_missing"] is False
    assert bundle.uncertainty_flags["tariff_uncertain"] is False
    assert bundle.freshness_summary["status"] == "fresh"
    assert bundle.confidence_summary["status"] in {"high", "medium"}
    assert part.enrichment_json["phase1"]["legacy"] is True
    assert part.enrichment_json["phase2a"]["offer_evidence"]["selected_offer_id"] == offer.id


def test_phase2a_evidence_assembly_returns_explicit_uncertainties_and_preserves_phase1(
    db_session,
    test_user,
    test_org,
):
    bom, project, part = _make_project_bundle(db_session, test_user, test_org)

    bundle = phase2a_evidence_service.assemble_for_bom_part(
        db_session,
        bom_part=part,
        bom=bom,
        project=project,
        target_currency="USD",
        lookup_date=_utc(2026, 4, 1),
    )

    assert bundle.offer_evidence["uncertain"] is True
    assert bundle.offer_evidence["uncertainty_reason"] == "offer_missing"
    assert bundle.availability_evidence["uncertainty_reason"] == "availability_missing"
    assert bundle.tariff_evidence["uncertain"] is True
    assert bundle.freight_evidence["uncertain"] is True
    assert bundle.uncertainty_flags["offer_missing"] is True
    assert bundle.uncertainty_flags["freight_uncertain"] is True
    assert any("Phase 1" in note for note in bundle.notes)
    assert part.enrichment_json["phase1"]["legacy"] is True
    assert part.enrichment_json["phase2a"]["uncertainty_flags"]["offer_missing"] is True