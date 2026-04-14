from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.integrations.distributor_connector import ProductDataConnector
from app.models.bom import BOMPart
from app.models.enrichment import (
    PartToSkuMapping,
    SKUAvailabilitySnapshot,
    SKUOffer,
)
from app.schemas.enrichment import (
    AvailabilityDTO,
    OfferDTO,
    PartIdentity,
    PriceBreakDTO,
    ProductSearchCandidate,
)
from app.services.enrichment.availability_ingestion_service import (
    availability_ingestion_service,
)
from app.services.enrichment.offer_ingestion_service import offer_ingestion_service
from app.services.enrichment.part_mapping_service import part_mapping_service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FakeConnector(ProductDataConnector):
    provider_name = "fake"

    def __init__(self):
        self.search_calls = 0
        self.offer_calls = 0
        self.availability_calls = 0

    def search_products(self, identity: PartIdentity) -> list[ProductSearchCandidate]:
        self.search_calls += 1
        return [
            ProductSearchCandidate(
                vendor_id=None,
                vendor_sku="SKU-123",
                manufacturer=identity.manufacturer or "Acme",
                mpn=identity.mpn or "MPN-1",
                normalized_mpn=identity.normalized_mpn or "MPN-1",
                canonical_part_key=identity.canonical_part_key,
                match_method="connector_exact",
                match_score=Decimal("0.93"),
                mapping_status="resolved",
                source_system="fake",
                source_record_id="prod-1",
                source_metadata={"provider_item_id": "prod-1"},
                is_preferred=True,
            )
        ]

    def fetch_offers(self, candidate: ProductSearchCandidate) -> list[OfferDTO]:
        self.offer_calls += 1
        return [
            OfferDTO(
                vendor_id=candidate.vendor_id,
                vendor_sku=candidate.vendor_sku,
                offer_name="Standard Reel",
                offer_status="ACTIVE",
                currency="USD",
                uom="ea",
                moq=Decimal("10"),
                spq=Decimal("5"),
                lead_time_days=Decimal("7"),
                observed_at=_utcnow(),
                ttl_seconds=1800,
                source_system="fake",
                source_record_id="offer-1",
                source_metadata={"provider_offer_id": "offer-1"},
                price_breaks=[
                    PriceBreakDTO(
                        break_qty=Decimal("1"),
                        unit_price=Decimal("2.50"),
                        currency="USD",
                    ),
                    PriceBreakDTO(
                        break_qty=Decimal("100"),
                        unit_price=Decimal("1.90"),
                        currency="USD",
                    ),
                ],
            )
        ]

    def fetch_availability(self, candidate: ProductSearchCandidate) -> list[AvailabilityDTO]:
        self.availability_calls += 1
        return [
            AvailabilityDTO(
                vendor_sku=candidate.vendor_sku,
                availability_status="IN_STOCK",
                available_qty=Decimal("250"),
                on_order_qty=Decimal("500"),
                inventory_location="US-WH",
                factory_lead_time_days=Decimal("5"),
                snapshot_at=_utcnow(),
                ttl_seconds=600,
                source_system="fake",
                source_record_id="avail-1",
                source_metadata={"provider_snapshot_id": "avail-1"},
            )
        ]


def test_part_mapping_uses_existing_high_confidence_row(db_session, test_org):
    bom = db_session.query(BOMPart).count()  # force metadata ready
    assert bom >= 0

    from app.models.bom import BOM

    bom_row = BOM(
        organization_id=test_org.id,
        source_file_name="batch2.csv",
        status="INGESTED",
    )
    db_session.add(bom_row)
    db_session.flush()

    part = BOMPart(
        bom_id=bom_row.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Test resistor",
        quantity=Decimal("50"),
        manufacturer="Acme",
        mpn="R-100",
        canonical_part_key="resistor:acme:r-100",
        normalization_trace_json={"normalized_mpn": "R-100"},
    )
    db_session.add(part)
    db_session.flush()

    existing = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="R-100",
        normalized_mpn="R-100",
        vendor_sku="SKU-EXISTING",
        match_method="seeded",
        confidence=Decimal("0.96"),
        source_system="seed",
        source_record_id="seed-1",
        source_record_hash="seed-hash-1",
        source_metadata={"mapping_status": "resolved", "match_score": "0.96"},
    )
    db_session.add(existing)
    db_session.commit()

    connector = FakeConnector()
    rows = part_mapping_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
    )

    assert connector.search_calls == 0
    assert rows[0].vendor_sku == "SKU-EXISTING"


def test_part_mapping_calls_connector_when_missing(db_session, test_org):
    from app.models.bom import BOM

    bom_row = BOM(
        organization_id=test_org.id,
        source_file_name="batch2.csv",
        status="INGESTED",
    )
    db_session.add(bom_row)
    db_session.flush()

    part = BOMPart(
        bom_id=bom_row.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Test capacitor",
        quantity=Decimal("25"),
        manufacturer="Acme",
        mpn="C-220",
        canonical_part_key="capacitor:acme:c-220",
        normalization_trace_json={"normalized_mpn": "C-220"},
    )
    db_session.add(part)
    db_session.flush()

    connector = FakeConnector()
    rows = part_mapping_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
        trace_id="trace-1",
    )
    db_session.commit()

    assert connector.search_calls == 1
    assert len(rows) == 1
    assert rows[0].vendor_sku == "SKU-123"
    assert rows[0].match_method == "connector_exact"
    assert rows[0].source_metadata["mapping_status"] == "resolved"
    assert rows[0].source_metadata["match_score"] == "0.93"


def test_offer_ingestion_and_quantity_break_lookup(db_session, test_org):
    from app.models.bom import BOM

    bom_row = BOM(
        organization_id=test_org.id,
        source_file_name="batch2.csv",
        status="INGESTED",
    )
    db_session.add(bom_row)
    db_session.flush()

    part = BOMPart(
        bom_id=bom_row.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="MCU",
        quantity=Decimal("120"),
        manufacturer="Acme",
        mpn="MCU-1",
        canonical_part_key="mcu:acme:mcu-1",
        normalization_trace_json={"normalized_mpn": "MCU-1"},
    )
    db_session.add(part)
    db_session.flush()

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="MCU-1",
        normalized_mpn="MCU-1",
        vendor_sku="SKU-123",
        match_method="connector_exact",
        confidence=Decimal("0.93"),
        source_system="fake",
        source_record_id="prod-1",
        source_record_hash="prod-hash-1",
        source_metadata={"mapping_status": "resolved", "match_score": "0.93"},
    )
    db_session.add(mapping)
    db_session.flush()

    connector = FakeConnector()
    offers = offer_ingestion_service.ingest_for_mapping(
        db_session,
        mapping=mapping,
        connector=connector,
        bom_part=part,
    )
    db_session.commit()

    assert connector.offer_calls == 1
    assert len(offers) == 1
    assert offers[0].source_metadata["data_hash"]
    assert offers[0].source_metadata["ttl_seconds"] == 1800

    price = offer_ingestion_service.resolve_best_price_break(
        db_session,
        sku_offer_id=offers[0].id,
        quantity=Decimal("120"),
    )
    assert price is not None
    assert price.break_qty == Decimal("100")
    assert price.unit_price == Decimal("1.90")


def test_availability_uses_fresh_snapshot_before_connector(db_session, test_org):
    from app.models.bom import BOM

    bom_row = BOM(
        organization_id=test_org.id,
        source_file_name="batch2.csv",
        status="INGESTED",
    )
    db_session.add(bom_row)
    db_session.flush()

    part = BOMPart(
        bom_id=bom_row.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Connector",
        quantity=Decimal("50"),
        manufacturer="Acme",
        mpn="CON-1",
        canonical_part_key="connector:acme:con-1",
        normalization_trace_json={"normalized_mpn": "CON-1"},
    )
    db_session.add(part)
    db_session.flush()

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="CON-1",
        normalized_mpn="CON-1",
        vendor_sku="SKU-123",
        match_method="connector_exact",
        confidence=Decimal("0.90"),
        source_system="fake",
        source_record_id="prod-1",
        source_record_hash="prod-hash-1",
        source_metadata={"mapping_status": "resolved", "match_score": "0.90"},
    )
    db_session.add(mapping)
    db_session.flush()

    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        vendor_id=None,
        offer_name="Std",
        offer_status="ACTIVE",
        currency="USD",
        observed_at=_utcnow(),
        valid_from=_utcnow(),
        source_system="fake",
        source_record_id="offer-1",
        source_record_hash="offer-hash-1",
        source_metadata={"ttl_seconds": 600, "vendor_sku": "SKU-123"},
    )
    db_session.add(offer)
    db_session.flush()

    snapshot = SKUAvailabilitySnapshot(
        sku_offer_id=offer.id,
        availability_status="IN_STOCK",
        available_qty=Decimal("55"),
        inventory_location="US-WH",
        freshness_status="FRESH",
        snapshot_at=_utcnow(),
        source_system="fake",
        source_record_id="avail-1",
        source_record_hash="avail-hash-1",
        source_metadata={"ttl_seconds": 600, "feasibility_tag": "feasible_now"},
    )
    db_session.add(snapshot)
    db_session.commit()

    connector = FakeConnector()
    rows = availability_ingestion_service.get_or_refresh_latest(
        db_session,
        sku_offer=offer,
        mapping=mapping,
        connector=connector,
        bom_part=part,
        need_by_date=date.today() + timedelta(days=7),
    )

    assert connector.availability_calls == 0
    assert len(rows) == 1
    assert rows[0].source_metadata["feasibility_tag"] == "feasible_now"


def test_availability_refreshes_when_stale_and_sets_feasibility(db_session, test_org):
    from app.models.bom import BOM

    bom_row = BOM(
        organization_id=test_org.id,
        source_file_name="batch2.csv",
        status="INGESTED",
    )
    db_session.add(bom_row)
    db_session.flush()

    part = BOMPart(
        bom_id=bom_row.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Sensor",
        quantity=Decimal("80"),
        manufacturer="Acme",
        mpn="SNS-1",
        canonical_part_key="sensor:acme:sns-1",
        normalization_trace_json={"normalized_mpn": "SNS-1"},
    )
    db_session.add(part)
    db_session.flush()

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="SNS-1",
        normalized_mpn="SNS-1",
        vendor_sku="SKU-123",
        match_method="connector_exact",
        confidence=Decimal("0.88"),
        source_system="fake",
        source_record_id="prod-1",
        source_record_hash="prod-hash-1",
        source_metadata={"mapping_status": "resolved", "match_score": "0.88"},
    )
    db_session.add(mapping)
    db_session.flush()

    offer = SKUOffer(
        part_to_sku_mapping_id=mapping.id,
        vendor_id=None,
        offer_name="Std",
        offer_status="ACTIVE",
        currency="USD",
        observed_at=_utcnow() - timedelta(hours=1),
        valid_from=_utcnow() - timedelta(hours=1),
        source_system="fake",
        source_record_id="offer-1",
        source_record_hash="offer-hash-1",
        source_metadata={"ttl_seconds": 10, "vendor_sku": "SKU-123"},
    )
    db_session.add(offer)
    db_session.flush()

    stale = SKUAvailabilitySnapshot(
        sku_offer_id=offer.id,
        availability_status="LOW_STOCK",
        available_qty=Decimal("5"),
        inventory_location="US-WH",
        freshness_status="FRESH",
        snapshot_at=_utcnow() - timedelta(hours=1),
        source_system="fake",
        source_record_id="avail-old",
        source_record_hash="avail-hash-old",
        source_metadata={"ttl_seconds": 10, "feasibility_tag": "unknown"},
    )
    db_session.add(stale)
    db_session.commit()

    connector = FakeConnector()
    rows = availability_ingestion_service.get_or_refresh_latest(
        db_session,
        sku_offer=offer,
        mapping=mapping,
        connector=connector,
        bom_part=part,
        need_by_date=date.today() + timedelta(days=7),
    )
    db_session.commit()

    assert connector.availability_calls == 1
    assert len(rows) == 1
    assert rows[0].availability_status == "IN_STOCK"
    assert rows[0].source_metadata["feasibility_tag"] == "feasible_now"