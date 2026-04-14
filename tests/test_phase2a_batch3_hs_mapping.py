from __future__ import annotations

from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.enrichment import HSMapping
from app.services.enrichment.hs_mapping_service import hs_mapping_service


def _make_bom_part(db_session, test_org, **overrides) -> BOMPart:
    bom = BOM(
        organization_id=test_org.id,
        source_file_name="batch3.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    part = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="NORMALIZED",
        row_number=1,
        description="Test part",
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


def test_hs_mapping_prefers_direct_bom_part_mapping(db_session, test_org):
    part = _make_bom_part(db_session, test_org)

    db_session.add(
        HSMapping(
            bom_part_id=None,
            canonical_part_key=part.canonical_part_key,
            category_code=part.category_code,
            material=part.material,
            hs_code="853400",
            hs_version="2022",
            jurisdiction="USA",
            mapping_method="seed",
            confidence=Decimal("0.91"),
            review_status="APPROVED",
            source_system="seed",
            source_record_id="canon-1",
            source_record_hash="canon-hash-1",
            source_metadata={},
        )
    )
    direct = HSMapping(
        bom_part_id=part.id,
        canonical_part_key=part.canonical_part_key,
        category_code=part.category_code,
        material=part.material,
        hs_code="853321",
        hs_version="2022",
        jurisdiction="USA",
        mapping_method="manual",
        confidence=Decimal("0.98"),
        review_status="APPROVED",
        source_system="seed",
        source_record_id="direct-1",
        source_record_hash="direct-hash-1",
        source_metadata={"reviewed_by": "ops"},
    )
    db_session.add(direct)
    db_session.flush()

    result = hs_mapping_service.resolve_for_bom_part(
        db_session,
        bom_part=part,
        trace_id="trace-hs-1",
    )

    assert result.resolved is True
    assert result.resolution_status == "resolved"
    assert result.hs_code == "853321"
    assert result.matched_on == "bom_part_id"
    assert result.mapping_id == direct.id


def test_hs_mapping_uses_taxonomy_when_no_direct_or_canonical_match(db_session, test_org):
    part = _make_bom_part(
        db_session,
        test_org,
        canonical_part_key=None,
        category_code="fastener",
        material="steel",
    )

    db_session.add(
        HSMapping(
            bom_part_id=None,
            canonical_part_key=None,
            category_code="fastener",
            material="steel",
            hs_code="731815",
            hs_version="2022",
            jurisdiction="USA",
            mapping_method="rule_based",
            confidence=Decimal("0.84"),
            review_status="AUTO",
            source_system="seed",
            source_record_id="tax-1",
            source_record_hash="tax-hash-1",
            source_metadata={"rule": "fastener+steel"},
        )
    )
    db_session.flush()

    result = hs_mapping_service.resolve_for_bom_part(db_session, bom_part=part)

    assert result.resolved is True
    assert result.hs_code == "731815"
    assert result.matched_on == "category_material"
    assert result.resolution_status == "resolved"


def test_hs_mapping_returns_explicit_uncertainty_for_low_confidence_match(db_session, test_org):
    part = _make_bom_part(db_session, test_org)

    db_session.add(
        HSMapping(
            bom_part_id=None,
            canonical_part_key=part.canonical_part_key,
            category_code=part.category_code,
            material=part.material,
            hs_code="853400",
            hs_version="2022",
            jurisdiction="USA",
            mapping_method="seed",
            confidence=Decimal("0.42"),
            review_status="AUTO",
            source_system="seed",
            source_record_id="low-1",
            source_record_hash="low-hash-1",
            source_metadata={},
        )
    )
    db_session.flush()

    result = hs_mapping_service.resolve_for_bom_part(db_session, bom_part=part)

    assert result.resolved is False
    assert result.resolution_status == "needs_review"
    assert result.hs_code == "853400"
    assert result.uncertainty_reason == "low_confidence_mapping"
    assert result.matched_on == "canonical_part_key"


def test_hs_mapping_returns_explicit_uncertainty_when_missing(db_session, test_org):
    part = _make_bom_part(db_session, test_org, canonical_part_key="missing:key")

    result = hs_mapping_service.resolve_for_bom_part(db_session, bom_part=part)

    assert result.resolved is False
    assert result.resolution_status == "unresolved"
    assert result.hs_code is None
    assert result.uncertainty_reason == "no_hs_mapping_found"
    assert result.source_metadata["canonical_part_key"] == "missing:key"