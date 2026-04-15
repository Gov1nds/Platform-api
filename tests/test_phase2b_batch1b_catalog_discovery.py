from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.integrations.catalog_connector import CatalogSearchConnector
from app.models.bom import BOM, BOMPart
from app.models.canonical import CanonicalSKU, SourceSKULink
from app.models.enrichment import PartToSkuMapping
from app.schemas.canonical_catalog import CatalogSearchCandidate
from app.schemas.enrichment import PartIdentity
from app.services.enrichment.catalog_discovery_service import (
    catalog_discovery_service,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FakeCatalogConnector(CatalogSearchConnector):
    provider_name = "fake_catalog"

    def __init__(self, candidates: list[CatalogSearchCandidate] | None = None):
        self.calls = 0
        self._candidates = candidates or []

    def search_parts(self, part_identity: PartIdentity) -> list[CatalogSearchCandidate]:
        self.calls += 1
        return list(self._candidates)


def _make_bom_and_part(db_session, test_org, *, mpn: str, canonical_part_key: str) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch1b.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Phase 2B test part",
        quantity=Decimal("25"),
        manufacturer="Acme",
        mpn=mpn,
        canonical_part_key=canonical_part_key,
        normalization_trace_json={"normalized_mpn": mpn},
    )
    db_session.add(part)
    db_session.flush()
    return part


def test_reuses_existing_high_confidence_source_link(db_session, test_org):
    part = _make_bom_and_part(
        db_session,
        test_org,
        mpn="R-100",
        canonical_part_key="resistor:acme:r-100",
    )

    sku = CanonicalSKU(
        canonical_key="resistor:acme:r-100::Acme::R-100",
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="R-100",
        normalized_mpn="R-100",
        canonical_name="Resistor",
        confidence=Decimal("0.96"),
        source_metadata={},
    )
    db_session.add(sku)
    db_session.flush()

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="R-100",
        normalized_mpn="R-100",
        vendor_sku="SKU-100",
        match_method="connector_exact",
        confidence=Decimal("0.96"),
        source_system="seed",
        source_record_id="seed-100",
        source_record_hash="seed-hash-100",
        source_metadata={"mapping_status": "resolved"},
    )
    db_session.add(mapping)
    db_session.flush()

    link = SourceSKULink(
        canonical_sku_id=sku.id,
        part_to_sku_mapping_id=mapping.id,
        source_system="seed",
        external_sku_key="seed-100",
        vendor_sku="SKU-100",
        manufacturer="Acme",
        mpn="R-100",
        normalized_mpn="R-100",
        canonical_part_key=part.canonical_part_key,
        match_method="exact_match",
        confidence=Decimal("0.96"),
        link_status="ACTIVE",
        source_metadata={},
    )
    db_session.add(link)
    db_session.commit()

    connector = FakeCatalogConnector()
    result = catalog_discovery_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
        trace_id="reuse-1",
    )

    assert connector.calls == 0
    assert result.reused_existing_links is True
    assert result.connector_called is False
    assert len(result.source_sku_link_ids) == 1


def test_promotes_existing_phase2a_mapping_without_duplicate_links(db_session, test_org):
    part = _make_bom_and_part(
        db_session,
        test_org,
        mpn="C-220",
        canonical_part_key="capacitor:acme:c-220",
    )

    mapping = PartToSkuMapping(
        bom_part_id=part.id,
        vendor_id=None,
        canonical_part_key=part.canonical_part_key,
        manufacturer="Acme",
        mpn="C-220",
        normalized_mpn="C-220",
        vendor_sku="SKU-C220",
        match_method="connector_exact",
        confidence=Decimal("0.93"),
        source_system="phase2a",
        source_record_id="phase2a-c220",
        source_record_hash="phase2a-c220-hash",
        source_metadata={"mapping_status": "resolved"},
    )
    db_session.add(mapping)
    db_session.commit()

    connector = FakeCatalogConnector()
    result_first = catalog_discovery_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
        trace_id="promote-1",
    )
    db_session.commit()

    result_second = catalog_discovery_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
        trace_id="promote-2",
    )
    db_session.commit()

    links = (
        db_session.query(SourceSKULink)
        .all()
    )
    skus = db_session.query(CanonicalSKU).all()

    assert connector.calls == 0
    assert len(skus) == 1
    assert len(links) == 1
    assert result_first.discovered_candidate_count >= 1
    assert result_second.reused_existing_links is True


def test_calls_connector_discards_low_confidence_and_marks_ambiguous(db_session, test_org):
    part = _make_bom_and_part(
        db_session,
        test_org,
        mpn="MCU-1",
        canonical_part_key="mcu:acme:mcu-1",
    )

    connector = FakeCatalogConnector(
        candidates=[
            CatalogSearchCandidate(
                source_system="fake",
                external_sku_id="fake-1",
                vendor_sku="SKU-1",
                manufacturer="Acme",
                mpn="MCU-1",
                normalized_mpn="MCU-1",
                link_method="exact_match",
                link_confidence=Decimal("0.90"),
            ),
            CatalogSearchCandidate(
                source_system="fake",
                external_sku_id="fake-2",
                vendor_sku="SKU-2",
                manufacturer="Acme",
                mpn="MCU-1",
                normalized_mpn="MCU-1",
                link_method="exact_match",
                link_confidence=Decimal("0.91"),
            ),
            CatalogSearchCandidate(
                source_system="fake",
                external_sku_id="fake-low",
                vendor_sku="SKU-LOW",
                manufacturer="Acme",
                mpn="MCU-1X",
                normalized_mpn="MCU-1X",
                link_method="fuzzy_text",
                link_confidence=Decimal("0.20"),
            ),
        ]
    )

    result = catalog_discovery_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        connector=connector,
        trace_id="connector-1",
    )
    db_session.commit()

    links = db_session.query(SourceSKULink).order_by(SourceSKULink.id).all()
    mappings = db_session.query(PartToSkuMapping).order_by(PartToSkuMapping.id).all()

    assert connector.calls == 1
    assert result.connector_called is True
    assert result.ambiguous is True
    assert result.discarded_candidates == 1
    assert len(links) == 2
    assert len(mappings) == 2
    assert all((row.source_metadata or {}).get("ambiguous") is True for row in links)