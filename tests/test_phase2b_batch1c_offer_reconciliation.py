from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.canonical import CanonicalOfferSnapshot, CanonicalSKU, SourceSKULink
from app.models.enrichment import PartToSkuMapping, SKUOffer, SKUOfferPriceBreak
from app.services.enrichment.offer_reconciliation_service import (
    offer_reconciliation_service,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_bom_part(db_session, test_org, *, canonical_part_key: str, mpn: str) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch1c.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Phase 2B Batch 1C test part",
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


def _make_offer(
    db_session,
    *,
    mapping: PartToSkuMapping,
    source_system: str,
    source_record_id: str,
    unit_price: Decimal,
    break_qty: Decimal,
    currency: str = "USD",
    observed_at: datetime | None = None,
    valid_to: datetime | None = None,
    freshness_status: str = "FRESH",
) -> SKUOffer:
    observed_at = observed_at or _utcnow()
    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        vendor_id=mapping.vendor_id,
        offer_name=f"offer-{source_record_id}",
        offer_status="ACTIVE",
        currency=currency,
        uom="EA",
        observed_at=observed_at,
        valid_from=observed_at,
        valid_to=valid_to,
        freshness_status=freshness_status,
        source_system=source_system,
        source_record_id=source_record_id,
        source_record_hash=f"offer:{source_system}:{source_record_id}",
        source_metadata={},
    )
    db_session.add(offer)
    db_session.flush()

    price_break = SKUOfferPriceBreak(
        sku_offer_id=offer.id,
        break_qty=break_qty,
        unit_price=unit_price,
        currency=currency,
        valid_from=observed_at,
        valid_to=valid_to,
        source_record_hash=f"pb:{source_system}:{source_record_id}:{break_qty}",
        source_metadata={},
    )
    db_session.add(price_break)
    db_session.flush()
    return offer


def test_best_price_selection_chooses_lowest_valid_offer(db_session, test_org, monkeypatch):
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

    now = _utcnow()
    _make_offer(
        db_session,
        mapping=mapping_a,
        source_system="alpha",
        source_record_id="offer-a",
        unit_price=Decimal("2.10"),
        break_qty=Decimal("100"),
        observed_at=now - timedelta(hours=2),
        valid_to=now + timedelta(days=3),
    )
    best_offer = _make_offer(
        db_session,
        mapping=mapping_b,
        source_system="beta",
        source_record_id="offer-b",
        unit_price=Decimal("1.80"),
        break_qty=Decimal("100"),
        observed_at=now - timedelta(hours=1),
        valid_to=now + timedelta(days=5),
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.enrichment.offer_reconciliation_service.fx_service.get_rate",
        lambda db, base_currency, quote_currency: Decimal("1"),
    )

    result = offer_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        requested_quantity=Decimal("100"),
        as_of=now,
        target_currency="USD",
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalOfferSnapshot)
        .filter(CanonicalOfferSnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result is not None
    assert result.best_price == Decimal("1.80000000")
    assert result.best_source_system == "beta"
    assert result.best_external_offer_id == "offer-b"
    assert result.best_sku_offer_id == best_offer.id
    assert result.offer_count == 2
    assert evidence["best_external_offer_id"] == "offer-b"


def test_spread_calculation_marks_conflict(db_session, test_org, monkeypatch):
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

    now = _utcnow()
    _make_offer(
        db_session,
        mapping=mapping_a,
        source_system="alpha",
        source_record_id="offer-a",
        unit_price=Decimal("1.00"),
        break_qty=Decimal("100"),
        observed_at=now - timedelta(hours=2),
        valid_to=now + timedelta(days=5),
    )
    _make_offer(
        db_session,
        mapping=mapping_b,
        source_system="beta",
        source_record_id="offer-b",
        unit_price=Decimal("1.80"),
        break_qty=Decimal("100"),
        observed_at=now - timedelta(hours=1),
        valid_to=now + timedelta(days=5),
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.enrichment.offer_reconciliation_service.fx_service.get_rate",
        lambda db, base_currency, quote_currency: Decimal("1"),
    )

    result = offer_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        requested_quantity=Decimal("100"),
        as_of=now,
        target_currency="USD",
        conflict_spread_threshold=Decimal("1.5"),
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalOfferSnapshot)
        .filter(CanonicalOfferSnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result is not None
    assert result.price_spread == Decimal("1.8000")
    assert result.has_conflict is True
    assert evidence["has_conflict"] is True
    assert evidence["offer_count"] == 2


def test_stale_handling_falls_back_when_no_fresh_offer_exists(db_session, test_org, monkeypatch):
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

    now = _utcnow()
    _make_offer(
        db_session,
        mapping=mapping_a,
        source_system="gamma",
        source_record_id="offer-g",
        unit_price=Decimal("4.25"),
        break_qty=Decimal("50"),
        observed_at=now - timedelta(days=10),
        valid_to=now + timedelta(days=2),
        freshness_status="STALE",
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.enrichment.offer_reconciliation_service.fx_service.get_rate",
        lambda db, base_currency, quote_currency: Decimal("1"),
    )

    result = offer_reconciliation_service.reconcile_for_canonical_sku(
        db_session,
        canonical_sku_id=sku.id,
        requested_quantity=Decimal("100"),
        as_of=now,
        target_currency="USD",
        fresh_ttl_days=7,
    )
    db_session.commit()

    snapshot = (
        db_session.query(CanonicalOfferSnapshot)
        .filter(CanonicalOfferSnapshot.canonical_sku_id == sku.id)
        .first()
    )
    evidence = snapshot.evidence_metadata or {}

    assert result is not None
    assert result.is_stale is True
    assert result.best_price == Decimal("4.25000000")
    assert evidence["stale_fallback_used"] is True
    assert evidence["is_stale"] is True