from __future__ import annotations

from decimal import Decimal

from app.models.vendor import (
    Vendor,
    VendorEvidenceAttachment,
    VendorIdentityAlias,
    VendorImportBatch,
    VendorImportRow,
)
from app.services.enrichment.vendor_import_service import vendor_import_service
from app.services.enrichment.vendor_resolution_service import vendor_resolution_service


def _seed_vendor(db_session, *, name: str, legal_name: str | None = None, website: str | None = None) -> Vendor:
    row = Vendor(
        name=name,
        legal_name=legal_name or name,
        website=website,
        status="BASIC",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_partial_success_import_preserves_row_level_failures(db_session, test_org):
    csv_content = "name,legal_name,website,registration_number,certifications\nAcme Ltd,Acme Limited,https://acme.com,REG-1,ISO9001\n,,, ,\n"
    batch = vendor_import_service.stage_csv_batch(
        db_session,
        csv_content=csv_content,
        organization_id=test_org.id,
        source_type="buyer_approved_list",
        source_ref="buyer-list-1",
        file_name="vendors.csv",
    )
    result = vendor_import_service.process_staged_batch(db_session, batch=batch)

    assert result.batch.total_rows == 2
    assert result.batch.success_rows == 1
    assert result.batch.failed_rows == 1
    assert result.batch.status == "completed_with_errors"

    rows = db_session.query(VendorImportRow).filter(VendorImportRow.batch_id == batch.id).order_by(VendorImportRow.row_index.asc()).all()
    assert rows[0].status == "processed"
    assert rows[1].status == "failed_validation"
    assert rows[1].validation_errors


def test_alias_creation_attaches_to_existing_vendor_on_strong_match(db_session):
    vendor = _seed_vendor(db_session, name="Acme Inc", legal_name="Acme Incorporated", website="https://acme.com")
    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor,
        aliases=[
            {
                "alias_type": "domain",
                "alias_value": "acme.com",
                "normalized_value": "acme.com",
                "confidence": Decimal("0.98"),
                "source_metadata": {"seed": True},
            }
        ],
        provenance="seed",
        source_ref="seed-acme",
    )

    result = vendor_resolution_service.resolve_vendor(
        db_session,
        raw_identity={"name": "Acme", "website": "https://acme.com", "source_type": "buyer_approved_list"},
    )
    assert result.status == "strong_match"
    assert result.vendor is not None
    assert result.vendor.id == vendor.id

    aliases = db_session.query(VendorIdentityAlias).filter(VendorIdentityAlias.vendor_id == vendor.id).all()
    assert len(aliases) == 1
    assert aliases[0].alias_type == "domain"


def test_resolution_distinguishes_strong_vs_weak_match(db_session):
    vendor_a = _seed_vendor(db_session, name="Alpha Components", legal_name="Alpha Components Pvt Ltd")
    vendor_b = _seed_vendor(db_session, name="Alpha Components EU", legal_name="Alpha Components Private Limited")
    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor_a,
        aliases=[{"alias_type": "legal_name", "alias_value": "Alpha Components Pvt Ltd", "normalized_value": "alpha components", "confidence": Decimal("0.86")}],
        provenance="seed",
    )
    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor_b,
        aliases=[{"alias_type": "legal_name", "alias_value": "Alpha Components Private Limited", "normalized_value": "alpha components", "confidence": Decimal("0.86")}],
        provenance="seed",
    )

    weak = vendor_resolution_service.resolve_vendor(
        db_session,
        raw_identity={"legal_name": "Alpha Components Pvt. Ltd.", "source_type": "buyer_approved_list"},
    )
    assert weak.status == "weak_match"
    assert weak.vendor is not None
    assert len(weak.candidates) >= 2

    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor_a,
        aliases=[{"alias_type": "registration_number", "alias_value": "REG-ALPHA-001", "normalized_value": "REGALPHA001", "confidence": Decimal("0.99")}],
        provenance="seed",
    )
    strong = vendor_resolution_service.resolve_vendor(
        db_session,
        raw_identity={"legal_name": "Alpha Components Pvt. Ltd.", "registration_number": "REG-ALPHA-001", "source_type": "buyer_approved_list"},
    )
    assert strong.status == "strong_match"
    assert strong.vendor is not None
    assert strong.vendor.id == vendor_a.id


def test_duplicate_collision_handling_is_visible_and_non_destructive(db_session, test_org):
    vendor_a = _seed_vendor(db_session, name="Orbit Manufacturing", legal_name="Orbit Manufacturing LLC")
    vendor_b = _seed_vendor(db_session, name="Orbit MFG EU", legal_name="Orbit Manufacturing Limited")
    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor_a,
        aliases=[{"alias_type": "legal_name", "alias_value": vendor_a.legal_name, "normalized_value": "orbit manufacturing", "confidence": Decimal("0.86")}],
        provenance="seed",
    )
    vendor_resolution_service.attach_aliases(
        db_session,
        vendor=vendor_b,
        aliases=[{"alias_type": "legal_name", "alias_value": vendor_b.legal_name, "normalized_value": "orbit manufacturing", "confidence": Decimal("0.86")}],
        provenance="seed",
    )

    result = vendor_import_service.ingest_buyer_vendor_rows(
        db_session,
        organization_id=test_org.id,
        rows=[{"legal_name": "Orbit Manufacturing", "website": "", "country": "US"}],
        source_ref="collision-list",
    )
    row = result.rows[0]
    assert row.status == "duplicate_collision"
    assert row.resolution_status == "weak_match"
    assert row.created_vendor_id is None
    assert row.warnings
    assert db_session.query(Vendor).count() == 2


def test_idempotent_reimport_behavior_reuses_batch_and_vendor_without_duplication(db_session, test_org):
    rows = [
        {
            "name": "Nova Circuits",
            "legal_name": "Nova Circuits GmbH",
            "website": "https://nova.example",
            "registration_number": "NOVA-7788",
            "certifications": "ISO13485,ISO9001",
            "capabilities": "pcb_assembly,testing",
            "country": "DE",
        }
    ]

    first = vendor_import_service.ingest_buyer_vendor_rows(
        db_session,
        organization_id=test_org.id,
        rows=rows,
        source_ref="approved-list-idempotent",
    )
    second = vendor_import_service.ingest_buyer_vendor_rows(
        db_session,
        organization_id=test_org.id,
        rows=rows,
        source_ref="approved-list-idempotent",
    )

    batches = db_session.query(VendorImportBatch).filter(VendorImportBatch.source_ref == "approved-list-idempotent").all()
    assert len(batches) == 1
    vendors = db_session.query(Vendor).filter(Vendor.legal_name == "Nova Circuits GmbH").all()
    assert len(vendors) == 1
    aliases = db_session.query(VendorIdentityAlias).filter(VendorIdentityAlias.vendor_id == vendors[0].id).all()
    assert {alias.alias_type for alias in aliases} >= {"domain", "legal_name", "registration_number"}
    evidence = db_session.query(VendorEvidenceAttachment).filter(VendorEvidenceAttachment.vendor_id == vendors[0].id).all()
    assert any(item.evidence_type == "capability" for item in evidence)
    assert any(item.evidence_type == "certification" for item in evidence)
    assert first.rows[0].created_vendor_id == vendors[0].id
    assert second.rows[0].matched_vendor_id == vendors[0].id