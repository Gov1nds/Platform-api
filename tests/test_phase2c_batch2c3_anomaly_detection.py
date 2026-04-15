from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.canonical import CanonicalAvailabilitySnapshot, CanonicalOfferSnapshot, CanonicalSKU, SourceSKULink
from app.models.enrichment import PartToSkuMapping, SKUAvailabilitySnapshot, SKUOffer, SKUOfferPriceBreak
from app.models.outcomes import AnomalyFlag
from app.models.vendor import Vendor
from app.services.anomaly_detection_service import anomaly_detection_service
from app.services.outcome_data_service import outcome_data_service


def _make_bom(db_session, test_org):
    row = BOM(
        organization_id=test_org.id,
        source_file_name="phase2c-batch2c3.csv",
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
        quantity=Decimal("10"),
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


def _make_mapping_bundle(db_session, part, vendor, *, canonical_key: str = "canon-1"):
    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=vendor.id,
        vendor_sku=f"SKU-{part.row_number}",
        source_system="test",
        source_record_id=f"map-{part.id}-{vendor.id}",
        source_record_hash=f"map-hash-{part.id}-{vendor.id}",
    )
    db_session.add(mapping)
    db_session.flush()

    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        vendor_id=vendor.id,
        source_system="test",
        source_record_id=f"offer-{part.id}-{vendor.id}",
        source_record_hash=f"offer-hash-{part.id}-{vendor.id}",
    )
    db_session.add(offer)
    db_session.flush()

    canonical = CanonicalSKU(
        canonical_key=canonical_key,
        canonical_part_key=f"pk-{part.id}",
        manufacturer="TestCo",
        normalized_mpn=f"MPN-{part.row_number}",
    )
    db_session.add(canonical)
    db_session.flush()

    link = SourceSKULink(
        canonical_sku_id=canonical.id,
        part_to_sku_mapping_id=mapping.id,
        vendor_id=vendor.id,
        source_system="test",
        external_sku_key=f"ext-{offer.id}",
    )
    db_session.add(link)
    db_session.flush()

    return mapping, offer, canonical, link


def test_price_anomaly_detection_flags_outlier_quote(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    _, _, _, link = _make_mapping_bundle(db_session, part, vendor)

    CanonicalOfferSnapshot(
        canonical_sku_id=link.canonical_sku_id,
        source_sku_link_id=link.id,
        vendor_id=vendor.id,
        unit_price=Decimal("10.00"),
        observed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )

    for idx, price in enumerate(["10.00", "10.20", "9.80"], start=1):
        db_session.add(CanonicalOfferSnapshot(
            canonical_sku_id=link.canonical_sku_id,
            source_sku_link_id=link.id,
            vendor_id=vendor.id,
            unit_price=Decimal(price),
            observed_at=datetime(2026, 4, idx, tzinfo=timezone.utc),
            evidence_metadata={"seed": True},
        ))
    db_session.flush()

    outcome = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("45.00"),
        quote_date=date(2026, 4, 10),
    )

    flags = anomaly_detection_service.detect_price_anomalies(db_session, quote_outcome_ids=[outcome.id])

    assert len(flags) == 1
    assert flags[0].entity_type == "quote_outcome"
    assert flags[0].metric_name == "quoted_price"
    assert flags[0].anomaly_type == "price_outlier"
    assert flags[0].severity == "high"


def test_lead_time_anomaly_detection_flags_large_delay_variance(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    seed_rows = [
        (date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 12)),
        (date(2026, 4, 3), date(2026, 4, 4), date(2026, 4, 14)),
        (date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 16)),
        (date(2026, 4, 7), date(2026, 4, 8), date(2026, 4, 18)),
    ]
    for quote_date, order_date, delivery_date in seed_rows:
        outcome_data_service.ingest_quote_outcome(
            db_session,
            bom_line_id=part.id,
            vendor_id=vendor.id,
            quoted_lead_time=Decimal("10"),
            quote_date=quote_date,
            order_date=order_date,
            delivery_date=delivery_date,
        )
    db_session.flush()
    outcome_data_service.sync_lead_time_history(db_session, vendor_ids=[vendor.id])

    outlier = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_lead_time=Decimal("10"),
        quote_date=date(2026, 4, 20),
        order_date=date(2026, 4, 21),
        delivery_date=date(2026, 5, 20),
    )
    db_session.flush()
    history_row = outcome_data_service.record_lead_time_history_for_outcome(db_session, quote_outcome_id=outlier.id)

    flags = anomaly_detection_service.detect_lead_time_anomalies(db_session, lead_time_history_ids=[history_row.id])

    assert len(flags) == 1
    assert flags[0].entity_type == "lead_time_history"
    assert flags[0].metric_name == "lead_time_diff_days"
    assert flags[0].anomaly_type == "lead_time_diff_outlier"
    assert flags[0].severity in {"medium", "high"}


def test_availability_anomaly_detection_flags_abrupt_jump(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)
    _, offer, _, _ = _make_mapping_bundle(db_session, part, vendor)

    earlier = SKUAvailabilitySnapshot(
        sku_offer_id=offer.id,
        availability_status="OUT_OF_STOCK",
        available_qty=Decimal("0"),
        inventory_location="WH-A",
        snapshot_at=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
        source_system="test",
        source_record_hash="avail-prev",
    )
    later = SKUAvailabilitySnapshot(
        sku_offer_id=offer.id,
        availability_status="IN_STOCK",
        available_qty=Decimal("5000"),
        inventory_location="WH-A",
        snapshot_at=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
        source_system="test",
        source_record_hash="avail-next",
    )
    db_session.add_all([earlier, later])
    db_session.flush()

    flags = anomaly_detection_service.detect_availability_anomalies(
        db_session,
        sku_availability_snapshot_ids=[later.id],
    )

    assert len(flags) == 1
    assert flags[0].entity_type == "sku_availability_snapshot"
    assert flags[0].anomaly_type == "availability_jump"
    assert flags[0].severity == "high"


def test_duplicate_anomaly_suppression_returns_single_row(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)
    _, _, canonical, _ = _make_mapping_bundle(db_session, part, vendor, canonical_key="canon-dupe")

    db_session.add(CanonicalAvailabilitySnapshot(
        canonical_sku_id=canonical.id,
        availability_status="IN_STOCK",
        available_qty=Decimal("-5"),
        inventory_location="WH-B",
        snapshot_at=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
        source_availability_snapshot_id=None,
    ))
    db_session.flush()
    row = db_session.query(CanonicalAvailabilitySnapshot).order_by(CanonicalAvailabilitySnapshot.created_at.desc()).first()

    first = anomaly_detection_service.detect_availability_anomalies(
        db_session,
        canonical_availability_snapshot_ids=[row.id],
    )
    second = anomaly_detection_service.detect_availability_anomalies(
        db_session,
        canonical_availability_snapshot_ids=[row.id],
    )

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id
    assert db_session.query(AnomalyFlag).count() == 1