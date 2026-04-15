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
from app.services.scoring.vendor_scorer import score_vendor

try:
    from app.models.canonical import (
        CanonicalAvailabilitySnapshot,
        CanonicalOfferSnapshot,
        CanonicalSKU,
    )
except Exception:  # pragma: no cover
    CanonicalSKU = None
    CanonicalOfferSnapshot = None
    CanonicalAvailabilitySnapshot = None


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_project_bundle(db_session, test_user, test_org):
    bom = BOM(
        uploaded_by_user_id=test_user.id,
        organization_id=test_org.id,
        source_file_name="phase2b-batch1f.csv",
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
        name="Phase2B Batch1F",
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


def _seed_raw_phase2a_evidence(db_session, *, part: BOMPart):
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

    db_session.add(
        SKUOfferPriceBreak(
            sku_offer_id=offer.id,
            break_qty=Decimal("100"),
            unit_price=Decimal("1.90"),
            currency="USD",
            price_type="unit",
            valid_from=_utc(2026, 1, 1),
            source_record_hash="break-hash-2",
            source_metadata={},
        )
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
    db_session.flush()
    return mapping, offer


def _seed_tariff_and_freight(db_session, *, part: BOMPart):
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


def test_canonical_snapshot_preferred_over_raw_evidence(db_session, test_user, test_org):
    if CanonicalSKU is None:
        return

    bom, project, part = _make_project_bundle(db_session, test_user, test_org)
    _, raw_offer = _seed_raw_phase2a_evidence(db_session, part=part)
    _seed_tariff_and_freight(db_session, part=part)

    canonical_sku = CanonicalSKU(
        canonical_key="mcu:acme:mcu-1::Acme::MCU-1",
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="MCU-1",
        normalized_mpn="MCU-1",
        canonical_name="MCU",
        confidence=Decimal("0.95"),
        source_metadata={},
    )
    db_session.add(canonical_sku)
    db_session.flush()

    db_session.add(
        CanonicalOfferSnapshot(
            canonical_sku_id=canonical_sku.id,
            source_offer_id=raw_offer.id,
            offer_status="ACTIVE",
            currency="USD",
            unit_price=Decimal("1.55"),
            freshness_status="FRESH",
            observed_at=_utc(2026, 4, 1),
            valid_from=_utc(2026, 4, 1),
            evidence_metadata={
                "best_price": "1.55",
                "best_currency": "USD",
                "best_source_system": "consolidated",
                "best_external_offer_id": "ext-offer-1",
                "best_sku_offer_id": raw_offer.id,
                "price_spread": "1.1200",
                "offer_count": 3,
                "is_stale": False,
            },
            source_metadata={"origin_country": "CHN", "origin_region": "Shenzhen"},
        )
    )
    db_session.add(
        CanonicalAvailabilitySnapshot(
            canonical_sku_id=canonical_sku.id,
            source_offer_id=raw_offer.id,
            availability_status="IN_STOCK",
            available_qty=Decimal("400"),
            freshness_status="FRESH",
            snapshot_at=_utc(2026, 4, 1),
            evidence_metadata={
                "source_systems": ["alpha", "beta"],
                "freshness_minutes": 10,
                "has_conflict": False,
                "lead_time_days": "3",
            },
            source_metadata={},
        )
    )
    db_session.commit()

    bundle = phase2a_evidence_service.assemble_for_bom_part(
        db_session,
        bom_part=part,
        bom=bom,
        project=project,
        target_currency="USD",
        lookup_date=_utc(2026, 4, 1),
    )

    assert bundle.offer_evidence["primary_source"] == "canonical_snapshot"
    assert bundle.offer_evidence["selected_price_break"]["unit_price"] == "1.55"
    assert bundle.offer_evidence["best_external_offer_id"] == "ext-offer-1"
    assert bundle.availability_evidence["primary_source"] == "canonical_snapshot"
    assert bundle.availability_evidence["available_qty"] == "400"


def test_fallback_to_phase2a_when_canonical_snapshot_missing(db_session, test_user, test_org):
    bom, project, part = _make_project_bundle(db_session, test_user, test_org)
    _, raw_offer = _seed_raw_phase2a_evidence(db_session, part=part)
    _seed_tariff_and_freight(db_session, part=part)
    db_session.commit()

    bundle = phase2a_evidence_service.assemble_for_bom_part(
        db_session,
        bom_part=part,
        bom=bom,
        project=project,
        target_currency="USD",
        lookup_date=_utc(2026, 4, 1),
    )

    assert bundle.offer_evidence["primary_source"] == "phase2a_raw"
    assert bundle.offer_evidence["selected_offer_id"] == raw_offer.id
    assert bundle.offer_evidence["selected_price_break"]["unit_price"] == "1.90"
    assert bundle.availability_evidence["primary_source"] == "phase2a_raw"
    assert bundle.availability_evidence["availability_status"] == "IN_STOCK"


def test_freshness_and_conflict_propagation_affect_scoring():
    phase2a_bundle = {
        "offer_evidence": {
            "primary_source": "canonical_snapshot",
            "vendor_id": None,
            "freshness_status": "STALE",
            "conflict_detected": True,
            "selected_price_break": {"unit_price": "1.20"},
            "source_metadata": {"best_price": "1.20"},
        },
        "availability_evidence": {
            "primary_source": "canonical_snapshot",
            "freshness_status": "FRESH",
            "has_conflict": True,
            "feasible": True,
        },
        "tariff_evidence": {"freshness_status": "FRESH"},
        "freight_evidence": {"freshness_status": "FRESH"},
        "freshness_summary": {
            "status": "mixed",
            "offer_status": "stale",
            "availability_status": "fresh",
            "tariff_status": "fresh",
            "freight_status": "fresh",
        },
        "confidence_summary": {"score": 0.62},
        "uncertainty_flags": {
            "offer_missing": False,
            "availability_missing": False,
            "tariff_uncertain": False,
            "freight_uncertain": False,
            "hs_uncertain": False,
            "canonical_offer_conflict": True,
            "canonical_availability_conflict": True,
            "canonical_offer_stale": True,
            "canonical_availability_stale": False,
        },
    }

    vendor = {
        "id": "vendor-1",
        "name": "Vendor One",
        "typical_unit_price": 1.40,
        "avg_lead_time_days": 14,
        "reliability_score": 0.82,
        "regions_served": ["Dallas"],
        "certifications": [],
        "capacity_profile": {"monthly_capacity": 1000},
        "capabilities": [{"process": "electronics", "material_family": "copper"}],
    }
    requirements = {
        "processes": ["electronics"],
        "materials": ["copper"],
        "target_lead_time_days": 30,
        "delivery_region": "Dallas",
        "required_certifications": [],
        "total_quantity": 120,
    }

    result_with_conflict = score_vendor(
        vendor,
        requirements=requirements,
        market_ctx={
            "fx_rate": 1.0,
            "freight_per_kg": 0.0,
            "data_age_days": 2,
            "market_median_price": 2.00,
            "phase2a": phase2a_bundle,
        },
    )
    result_without_conflict = score_vendor(
        vendor,
        requirements=requirements,
        market_ctx={
            "fx_rate": 1.0,
            "freight_per_kg": 0.0,
            "data_age_days": 2,
            "market_median_price": 2.00,
            "phase2a": {
                **phase2a_bundle,
                "freshness_summary": {
                    "status": "fresh",
                    "offer_status": "fresh",
                    "availability_status": "fresh",
                    "tariff_status": "fresh",
                    "freight_status": "fresh",
                },
                "uncertainty_flags": {
                    **phase2a_bundle["uncertainty_flags"],
                    "canonical_offer_conflict": False,
                    "canonical_availability_conflict": False,
                    "canonical_offer_stale": False,
                },
            },
        },
    )

    assert result_with_conflict["canonical_snapshot_used"] is True
    assert result_with_conflict["breakdown"]["evidence_confidence"] < result_without_conflict["breakdown"]["evidence_confidence"]
    assert result_with_conflict["breakdown"]["freshness_adjustment"] < result_without_conflict["breakdown"]["freshness_adjustment"]