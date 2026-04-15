from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.vendor import (
    Vendor,
    VendorEvidenceAttachment,
    VendorIdentityAlias,
    VendorImportBatch,
    VendorImportRow,
)
from app.services.enrichment.vendor_resolution_service import vendor_resolution_service


CONFIDENCE_TIER_BY_SOURCE = {
    "buyer_approved_list": "buyer_asserted",
    "vendor_submitted": "vendor_asserted",
    "platform_seed": "platform_seeded",
}


@dataclass
class VendorImportProcessResult:
    batch: VendorImportBatch
    rows: list[VendorImportRow]


class VendorImportService:
    def stage_csv_batch(
        self,
        db: Session,
        *,
        csv_content: str,
        organization_id: str | None,
        created_by_user_id: str | None = None,
        source_type: str = "buyer_approved_list",
        source_ref: str | None = None,
        file_name: str | None = None,
    ) -> VendorImportBatch:
        file_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
        batch = (
            db.query(VendorImportBatch)
            .filter(
                VendorImportBatch.organization_id == organization_id,
                VendorImportBatch.source_type == source_type,
                VendorImportBatch.source_ref == source_ref,
            )
            .first()
        )
        if batch is None:
            batch = VendorImportBatch(
                organization_id=organization_id,
                created_by_user_id=created_by_user_id,
                source_type=source_type,
                source_ref=source_ref,
                file_name=file_name,
                file_hash=file_hash,
                status="staged",
            )
            db.add(batch)
            db.flush()
        else:
            batch.file_name = file_name or batch.file_name
            batch.file_hash = file_hash
            batch.status = "staged"
            db.flush()

        reader = csv.DictReader(io.StringIO(csv_content))
        row_count = 0
        for index, raw in enumerate(reader, start=1):
            row_count += 1
            idempotency_key = self._row_idempotency_key(batch.id, index, raw)
            normalized_identity = vendor_resolution_service.normalize_identity({**raw, "source_type": source_type})
            parse_errors = self._parse_errors(raw)
            validation_errors = self._validation_errors(raw, normalized_identity)

            row = (
                db.query(VendorImportRow)
                .filter(VendorImportRow.batch_id == batch.id, VendorImportRow.row_index == index)
                .first()
            )
            if row is None:
                row = VendorImportRow(
                    batch_id=batch.id,
                    row_index=index,
                    idempotency_key=idempotency_key,
                    raw_row=dict(raw),
                    normalized_identity=normalized_identity,
                    parse_errors=parse_errors,
                    validation_errors=validation_errors,
                    source_confidence_tier=CONFIDENCE_TIER_BY_SOURCE.get(source_type, "imported"),
                    source_metadata={"file_hash": file_hash},
                )
                db.add(row)
            else:
                row.idempotency_key = idempotency_key
                row.raw_row = dict(raw)
                row.normalized_identity = normalized_identity
                row.parse_errors = parse_errors
                row.validation_errors = validation_errors
                row.source_confidence_tier = CONFIDENCE_TIER_BY_SOURCE.get(source_type, "imported")
                row.source_metadata = {**(row.source_metadata or {}), "file_hash": file_hash}
                if row.status not in {"processed", "weak_match", "duplicate_collision", "failed_validation"}:
                    row.status = "staged"
            db.flush()

        batch.total_rows = row_count
        batch.updated_at = self._now()
        db.flush()
        return batch

    def process_staged_batch(self, db: Session, *, batch: VendorImportBatch) -> VendorImportProcessResult:
        rows = (
            db.query(VendorImportRow)
            .filter(VendorImportRow.batch_id == batch.id)
            .order_by(VendorImportRow.row_index.asc())
            .all()
        )
        batch.status = "processing"
        batch.processed_rows = 0
        batch.success_rows = 0
        batch.failed_rows = 0
        batch.warning_rows = 0
        batch.duplicate_collision_rows = 0
        db.flush()

        for row in rows:
            self._process_row(db, batch=batch, row=row)

        batch.processed_rows = len(rows)
        batch.completed_at = self._now()
        if batch.failed_rows and batch.success_rows:
            batch.status = "completed_with_errors"
        elif batch.failed_rows:
            batch.status = "failed"
        else:
            batch.status = "completed"
        db.flush()
        return VendorImportProcessResult(batch=batch, rows=rows)

    def ingest_buyer_vendor_rows(
        self,
        db: Session,
        *,
        organization_id: str | None,
        rows: Iterable[dict[str, Any]],
        created_by_user_id: str | None = None,
        source_ref: str | None = None,
    ) -> VendorImportProcessResult:
        csv_buffer = io.StringIO()
        row_list = list(rows)
        fieldnames = sorted({key for row in row_list for key in row.keys()})
        writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in row_list:
            writer.writerow(row)
        batch = self.stage_csv_batch(
            db,
            csv_content=csv_buffer.getvalue(),
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            source_type="buyer_approved_list",
            source_ref=source_ref,
            file_name="buyer_vendor_list.csv",
        )
        return self.process_staged_batch(db, batch=batch)

    def _process_row(self, db: Session, *, batch: VendorImportBatch, row: VendorImportRow) -> None:
        if row.validation_errors:
            row.status = "failed_validation"
            row.resolution_status = "invalid"
            row.processed_at = self._now()
            batch.failed_rows += 1
            return

        raw = dict(row.raw_row or {})
        resolution = vendor_resolution_service.resolve_vendor(
            db,
            raw_identity={**raw, "source_type": batch.source_type},
        )
        row.normalized_identity = resolution.normalized_identity
        row.resolution_confidence = resolution.confidence
        row.collision_group_key = resolution.collision_key
        row.processed_at = self._now()

        if resolution.status == "weak_match":
            row.status = "duplicate_collision"
            row.resolution_status = "weak_match"
            row.matched_vendor_id = resolution.vendor.id if resolution.vendor else None
            row.warnings = [
                {
                    "code": "ambiguous_vendor_match",
                    "candidate_vendor_ids": [candidate.vendor.id for candidate in resolution.candidates],
                    "candidate_confidences": [str(candidate.confidence) for candidate in resolution.candidates],
                }
            ]
            batch.warning_rows += 1
            batch.duplicate_collision_rows += 1
            return

        if resolution.status == "strong_match" and resolution.vendor is not None:
            vendor = resolution.vendor
            row.status = "processed"
            row.resolution_status = "strong_match"
            row.matched_vendor_id = vendor.id
            vendor_resolution_service.attach_aliases(
                db,
                vendor=vendor,
                aliases=resolution.aliases_to_attach,
                source_batch=batch,
                source_row=row,
                provenance=batch.source_type,
                source_ref=batch.source_ref,
            )
            self._attach_evidence(db, vendor=vendor, batch=batch, row=row)
            batch.success_rows += 1
            return

        vendor = self._create_vendor_from_row(db, batch=batch, row=row)
        row.status = "processed"
        row.resolution_status = "new_vendor"
        row.created_vendor_id = vendor.id
        vendor_resolution_service.attach_aliases(
            db,
            vendor=vendor,
            aliases=resolution.aliases_to_attach,
            source_batch=batch,
            source_row=row,
            provenance=batch.source_type,
            source_ref=batch.source_ref,
        )
        self._attach_evidence(db, vendor=vendor, batch=batch, row=row)
        batch.success_rows += 1

    def _create_vendor_from_row(self, db: Session, *, batch: VendorImportBatch, row: VendorImportRow) -> Vendor:
        raw = row.raw_row or {}
        website = raw.get("website") or None
        vendor = Vendor(
            organization_id=batch.organization_id,
            name=(raw.get("name") or raw.get("legal_name") or raw.get("website") or "Imported Vendor").strip(),
            legal_name=(raw.get("legal_name") or raw.get("name") or None),
            country=raw.get("country") or None,
            website=website,
            contact_email=raw.get("contact_email") or None,
            status="BASIC",
            onboarding_method=batch.source_type,
            profile_completeness=35,
            identity_json={
                "registration_number": row.normalized_identity.get("registration_number"),
                "lei": row.normalized_identity.get("lei"),
                "source_batch_id": batch.id,
                "source_row_id": row.id,
            },
            metadata_={
                "import_source_type": batch.source_type,
                "import_source_ref": batch.source_ref,
                "source_confidence_tier": row.source_confidence_tier,
            },
        )
        db.add(vendor)
        db.flush()
        return vendor

    def _attach_evidence(self, db: Session, *, vendor: Vendor, batch: VendorImportBatch, row: VendorImportRow) -> None:
        raw = row.raw_row or {}
        certifications = self._csv_list(raw.get("certifications"))
        capabilities = self._csv_list(raw.get("capabilities"))
        source_confidence = self._confidence_for_tier(row.source_confidence_tier)

        existing = {
            (ev.evidence_type, ev.capability_key, ev.certification_name)
            for ev in db.query(VendorEvidenceAttachment)
            .filter(VendorEvidenceAttachment.vendor_id == vendor.id, VendorEvidenceAttachment.source_row_id == row.id)
            .all()
        }

        key = ("source_confidence", None, None)
        if key not in existing:
            db.add(VendorEvidenceAttachment(
                vendor_id=vendor.id,
                evidence_type="source_confidence",
                source_confidence=source_confidence,
                source_type=batch.source_type,
                source_ref=batch.source_ref,
                source_batch_id=batch.id,
                source_row_id=row.id,
                evidence_metadata={"source_confidence_tier": row.source_confidence_tier},
            ))

        for capability in capabilities:
            key = ("capability", capability, None)
            if key in existing:
                continue
            db.add(VendorEvidenceAttachment(
                vendor_id=vendor.id,
                evidence_type="capability",
                capability_key=capability,
                source_confidence=source_confidence,
                source_type=batch.source_type,
                source_ref=batch.source_ref,
                source_batch_id=batch.id,
                source_row_id=row.id,
                evidence_metadata={"raw_capabilities": capabilities},
            ))

        for certification in certifications:
            key = ("certification", None, certification)
            if key in existing:
                continue
            db.add(VendorEvidenceAttachment(
                vendor_id=vendor.id,
                evidence_type="certification",
                certification_name=certification,
                source_confidence=source_confidence,
                source_type=batch.source_type,
                source_ref=batch.source_ref,
                source_batch_id=batch.id,
                source_row_id=row.id,
                evidence_metadata={"raw_certifications": certifications},
            ))
        db.flush()

    def _row_idempotency_key(self, batch_id: str, row_index: int, raw: dict[str, Any]) -> str:
        payload = json.dumps({"batch_id": batch_id, "row_index": row_index, "raw": raw}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _parse_errors(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if not isinstance(raw, dict):
            errors.append({"code": "invalid_row_type", "message": "row must be a mapping"})
        return errors

    def _validation_errors(self, raw: dict[str, Any], normalized_identity: dict[str, str]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if not (raw.get("name") or raw.get("legal_name")):
            errors.append({"code": "missing_name", "message": "name or legal_name is required"})
        if not normalized_identity:
            errors.append({"code": "missing_identity", "message": "at least one identity signal is required"})
        return errors

    def _csv_list(self, value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            items = value
        else:
            items = str(value).replace(";", ",").split(",")
        return [item.strip() for item in items if str(item).strip()]

    def _confidence_for_tier(self, tier: str | None) -> Decimal:
        mapping = {
            "buyer_asserted": Decimal("0.90"),
            "vendor_asserted": Decimal("0.85"),
            "platform_seeded": Decimal("0.95"),
            "imported": Decimal("0.70"),
        }
        return mapping.get(tier or "imported", Decimal("0.70"))

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


vendor_import_service = VendorImportService()