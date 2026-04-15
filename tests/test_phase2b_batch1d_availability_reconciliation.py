from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.canonical import CanonicalAvailabilitySnapshot, CanonicalSKU, SourceSKULink
from app.models.enrichment import PartToSkuMapping, SKUOffer, SKUAvailabilitySnapshot
from app.services.enrichment.availability_reconciliation_service import (
    availability_reconciliation_service,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_bom_part(db_session, test_org, *, canonical_part_key: str, mpn: str) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch1d.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Phase 2B Batch 1D test part",
        quantity=Decimal("100"),
        manufacturer="Acme",
        mpn=mpn,
        canonical_part_key=canonical_part_key,
        normalization_trace_json={"normalized_mpn": mpn},
    )
    db_session.add(part)
    db_session.flush()
    return part


def _make_canonical_link(
    db_session,
    *,
    part: BOMPart,
    source_system: str,
    source_record_id: str,
    vendor_sku: str,
    confidence: Decimal = Decimal("0.95"),
) -> tuple[CanonicalSKU, PartToSkuMapping, SourceSKULink]:
    sku = CanonicalSKU(
        canonical_key=f"{part.canonical_part_key}::{source_system}::{vendor_sku}",
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn=part.mpn,
        normalized_mpn=part.mpn,
        canonical_name=part.description,
        confidence=confidence,
        source_metadata={},
    )
    db_session.add(sku)
    db_session.flush()

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn=part.mpn,
        normalized_mpn=part.mpn,
        vendor_sku=vendor_sku,
        match_method="connector_exact",
        confidence=confidence,
        source_system=source_system,
        source_record_id=source_record_id,
        source_record_hash=f"{source_system}:{source_record_id}",
        source_metadata={"mapping_status": "resolved"},
    )
    db_session.add(mapping)
    db_session.flush()

    link_kwargs = {
        "canonical_sku_id": sku.id,
        "part_to_sku_mapping_id": mapping.id,
        "source_system": source_system,
        "vendor_sku": vendor_sku,
        "manufacturer": "Acme",
        "mpn": part.mpn,
        "normalized_mpn": part.mpn,
        "canonical_part_key": part.canonical_part_key,
        "source_metadata": {},
    }
    if hasattr(SourceSKULink, "external_sku_id"):
        link_kwargs["external_sku_id"] = source_record_id
    else:
        link_kwargs["external_sku_key"] = source_record_id

    if hasattr(SourceSKULink, "link_method"):
        link_kwargs["link_method"] = "external_id"
    elif hasattr(SourceSKULink, "match_method"):
        link_kwargs["match_method"] = "external_id"

    if hasattr(SourceSKULink, "link_confidence"):
        link_kwargs["link_confidence"] = confidence
    else:
        link_kwargs["confidence"] = confidence

    if hasattr(SourceSKULink, "link_status"):
        link_kwargs["link_status"] = "ACTIVE"

    link = SourceSKULink(**link_kwargs)
    db_session.add(link)
    db_session.flush()

    return sku, mapping, link


def _make_offer(db_session, *, mapping: PartToSkuMapping, source_system: str, source_record_id: str) -> SKUOffer:
    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        vendor_id=mapping.vendor_id,
        offer_name=f"offer-{source_record_id}",
        offer_status="ACTIVE",
        currency="USD",
        uom="EA",
        observed_at=_utcnow(),
        valid_from=_utcnow(),
        valid_to=None,
        freshness_status="FRESH",
        source_system=source_system,
        source_record_id=source_record_id,
        source_record_hash=f"offer:{source_system}:{source_record_id}",
        source_metadata={},
    )
    db_session.add(offer)
    db_session.flush()
    return offer


def _make_snapshot(
    db_session,
    *,
    offer: SKUOffer,
    availability_status: str,
    source_system: str,
    available_qty: Decimal | None = None,
    lead_time_days: Decimal | None = None,
    snapshot_at: datetime | None = None,
) -> SKUAvailabilitySnapshot:
    snapshot = SKUAvailabilitySnapshot(
        sku_offer_id=offer.id,
        availability_status=availability_status,
        available_qty=available_qty,
        on_order_qty=None,
        allocated_qty=None,
        backorder_qty=None,
        moq=None,
        factory_lead_time_days=lead_time_days,
        inventory_location="MAIN",
        freshness_status="FRESH",
        snapshot_at=snapshot_at or _utcnow(),
        source_system=source_system,
        source_record_id=f"{source_system}:{offer.id}",
        source_record_hash=f"{source_system}:{offer.id}:{snapshot_at or 'latest'}",
        source_metadata={},
    )
    db_session.add(snapshot)
    db_session.flush()
    return snapshot


def test_merge_logic_prefers_in_stock_and_sums_available_qty(db_session, test_org):
    part = _make_bom_part(
        db_session,
        test_org,
        canonical_part_key="resistor:acme:r-100",
        mpn="R-100",
    )
    sku, mapping_a, _ = _make_canonical_link(
        db_session,
        part=part,
        source_system="alpha",
        source_record_id="sku-a",
        vendor_sku="SKU-A",
    )
    _, mapping_b, _ = _make_canonical_link(
        db_session,
        part=part,
        source_system="beta",
        source_record_id="sku-b",
        vendor_sku="SKU-B",
    )

    offer_a = _make_offer(db_session, mapping=mapping_a, source_system="alpha", source_record_id="offer-a")
    offer_b = _make_offer(db_session, mapping=mapping_b, source_system="beta", source_record_id="offer-b")

    _make_snapshot(
        db_session,
        offer=offer_a,
        availability_status="IN_STOCK",
        source_system="alpha",
        available_qty=Decimal("120"),
        snapshot_at=_utcnow() - timedelta(minutes=20),
    )
    _make_snapshot(
        db_session,
        offer=offer_b,
        availability_status="LIMITED_STOCK",
        source_system="beta",
        available_qty=Decimal("30"),
        snapshot_at=_utcnow() - timedelta(minutes=10),
    )
    db_session.commit()

    result = availability_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        as_of=_utcnow(),
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalAvailabilitySnapshot)
        .filter(CanonicalAvailabilitySnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result.availability_status == "IN_STOCK"
    assert result.available_qty == Decimal("150")
    assert set(result.source_systems) == {"alpha", "beta"}
    assert evidence["availability_status"] == "IN_STOCK"
    assert evidence["available_qty"] == "150"


def test_conflict_downgrade_to_limited_stock(db_session, test_org):
    part = _make_bom_part(
        db_session,
        test_org,
        canonical_part_key="capacitor:acme:c-220",
        mpn="C-220",
    )
    sku, mapping_a, _ = _make_canonical_link(
        db_session,
        part=part,
        source_system="alpha",
        source_record_id="sku-a",
        vendor_sku="SKU-A",
    )
    _, mapping_b, _ = _make_canonical_link(
        db_session,
        part=part,
        source_system="beta",
        source_record_id="sku-b",
        vendor_sku="SKU-B",
    )

    offer_a = _make_offer(db_session, mapping=mapping_a, source_system="alpha", source_record_id="offer-a")
    offer_b = _make_offer(db_session, mapping=mapping_b, source_system="beta", source_record_id="offer-b")

    _make_snapshot(
        db_session,
        offer=offer_a,
        availability_status="IN_STOCK",
        source_system="alpha",
        available_qty=Decimal("100"),
        snapshot_at=_utcnow() - timedelta(minutes=30),
    )
    _make_snapshot(
        db_session,
        offer=offer_b,
        availability_status="OUT_OF_STOCK",
        source_system="beta",
        available_qty=Decimal("0"),
        snapshot_at=_utcnow() - timedelta(minutes=5),
    )
    db_session.commit()

    result = availability_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        as_of=_utcnow(),
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalAvailabilitySnapshot)
        .filter(CanonicalAvailabilitySnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result.availability_status == "LIMITED_STOCK"
    assert result.has_conflict is True
    assert set(result.source_systems) == {"alpha", "beta"}
    assert evidence["has_conflict"] is True
    assert "conflict_downgraded_to_limited_stock" in result.notes


def test_unknown_fallback_when_no_reliable_data(db_session, test_org):
    part = _make_bom_part(
        db_session,
        test_org,
        canonical_part_key="sensor:acme:sns-1",
        mpn="SNS-1",
    )
    sku, mapping_a, _ = _make_canonical_link(
        db_session,
        part=part,
        source_system="gamma",
        source_record_id="sku-g",
        vendor_sku="SKU-G",
    )

    offer_a = _make_offer(db_session, mapping=mapping_a, source_system="gamma", source_record_id="offer-g")

    # invalid negative qty should be ignored
    _make_snapshot(
        db_session,
        offer=offer_a,
        availability_status="UNKNOWN",
        source_system="gamma",
        available_qty=Decimal("-5"),
        snapshot_at=_utcnow() - timedelta(minutes=15),
    )
    db_session.commit()

    result = availability_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        as_of=_utcnow(),
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalAvailabilitySnapshot)
        .filter(CanonicalAvailabilitySnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result.availability_status == "UNKNOWN"
    assert result.available_qty is None
    assert result.has_conflict is False
    assert result.source_systems == []
    assert evidence["availability_status"] == "UNKNOWN"
    assert "no_reliable_data" in result.notes