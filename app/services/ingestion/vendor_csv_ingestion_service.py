"""
Vendor CSV Ingestion service (Phase 3).

Implements Execution Plan §8 "Vendor Master Imports: bulk CSV uploads and
self-service onboarding. Include initial vetting, category tagging,
deduplication, and assignment of confidence levels."

Produces a VendorImportBatch + VendorImportRow audit trail and upserts
into pricing.vendors / vendor_locations / vendor_lead_time_bands, then
computes trust tier and logs duplicate candidates.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.vendor import (
    Vendor, VendorImportBatch, VendorImportRow,
    VendorLocation, VendorLeadTimeBand, VendorCapability,
)
from app.services.vendor_intelligence_service import vendor_intelligence_service

logger = logging.getLogger(__name__)


@dataclass
class IngestionRowResult:
    row_index: int
    status: str  # created | updated | skipped | failed
    vendor_id: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_of: str | None = None


@dataclass
class IngestionResult:
    batch_id: str
    total_rows: int = 0
    success: int = 0
    failed: int = 0
    warnings: int = 0
    duplicates_found: list[str] = field(default_factory=list)
    row_results: list[IngestionRowResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_rows": self.total_rows,
            "success": self.success,
            "failed": self.failed,
            "warnings": self.warnings,
            "duplicates_found": list(self.duplicates_found),
            "row_results": [
                {
                    "row_index": r.row_index,
                    "status": r.status,
                    "vendor_id": r.vendor_id,
                    "errors": list(r.errors),
                    "warnings": list(r.warnings),
                    "duplicate_of": r.duplicate_of,
                }
                for r in self.row_results
            ],
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _trim(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _upper2(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().upper()
    if not v:
        return None
    return v[:2]


def _is_valid_email(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip()))


def _normalize_phone(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"[^\d+]", "", value)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _parse_list(value: Any) -> list[str]:
    """Parse pipe-, semi-, or comma-separated lists."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    for sep in ("|", ";", ","):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


EXPECTED_COLUMNS = (
    "name", "legal_name", "trade_name", "country", "state_province", "city",
    "address_line1", "address_line2", "postal_code", "contact_email",
    "contact_phone", "website", "primary_category_tag",
    "secondary_category_tags", "certifications", "export_capable",
    "founded_year", "employee_count_band", "avg_lead_time_days",
    "default_currency", "moq", "moq_unit",
)


class VendorCSVIngestionService:
    """§8 CSV / self-service vendor ingestion."""

    def ingest_vendor_csv(
        self,
        file_content: bytes | str,
        org_id: str | None,
        created_by_user_id: str | None,
        db: Session,
        file_name: str | None = None,
        source_type: str = "buyer_approved_list",
        source_ref: str | None = None,
    ) -> IngestionResult:
        raw = file_content.decode("utf-8-sig") if isinstance(file_content, bytes) else file_content
        file_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        batch = VendorImportBatch(
            organization_id=org_id,
            created_by_user_id=created_by_user_id,
            source_type=source_type,
            source_ref=source_ref or (file_name or file_hash[:16]),
            file_name=file_name,
            file_hash=file_hash,
            status="processing",
            import_mode="upsert_safe",
            source_metadata={"columns_expected": list(EXPECTED_COLUMNS)},
        )
        db.add(batch)
        db.flush()

        result = IngestionResult(batch_id=batch.id)
        reader = csv.DictReader(io.StringIO(raw))

        for row_idx, row in enumerate(reader, start=1):
            row_result = self._process_row(
                row_idx=row_idx,
                row=row,
                batch=batch,
                org_id=org_id,
                db=db,
            )
            result.row_results.append(row_result)
            result.total_rows += 1
            if row_result.status in ("created", "updated"):
                result.success += 1
            elif row_result.status == "failed":
                result.failed += 1
            if row_result.warnings:
                result.warnings += 1
            if row_result.duplicate_of:
                result.duplicates_found.append(row_result.duplicate_of)

        batch.total_rows = result.total_rows
        batch.processed_rows = result.total_rows
        batch.success_rows = result.success
        batch.failed_rows = result.failed
        batch.warning_rows = result.warnings
        batch.status = "complete"
        batch.completed_at = _now()
        db.flush()

        logger.info(
            "vendor_csv_ingest batch=%s total=%d success=%d failed=%d duplicates=%d",
            batch.id, result.total_rows, result.success, result.failed, len(result.duplicates_found),
        )
        return result

    # ── Row processing ──────────────────────────────────────────────────

    def _process_row(
        self,
        row_idx: int,
        row: dict[str, Any],
        batch: VendorImportBatch,
        org_id: str | None,
        db: Session,
    ) -> IngestionRowResult:
        out = IngestionRowResult(row_index=row_idx, status="failed")

        name = _trim(row.get("name"))
        if not name:
            out.errors.append("missing_required_field:name")
            self._persist_row(batch, row_idx, row, out, None, db)
            return out

        # Validation / normalization
        email = _trim(row.get("contact_email"))
        if email and not _is_valid_email(email):
            out.warnings.append("malformed_email_dropped")
            email = ""
        phone = _normalize_phone(_trim(row.get("contact_phone")))
        country_iso2 = _upper2(row.get("country"))

        vendor_dict_for_fp = {
            "name": name,
            "legal_name": _trim(row.get("legal_name")),
            "country_iso2": country_iso2,
            "contact_email": email,
        }
        fingerprint = vendor_intelligence_service.compute_dedup_fingerprint(vendor_dict_for_fp)

        # Match existing vendor by fingerprint (same org preferred)
        existing_same_org = (
            db.query(Vendor)
            .filter(
                Vendor.dedup_fingerprint == fingerprint,
                Vendor.deleted_at.is_(None),
                Vendor.merged_into_vendor_id.is_(None),
                (Vendor.organization_id == org_id) if org_id else (Vendor.organization_id.is_(None)),
            )
            .first()
        )
        existing_any = (
            db.query(Vendor)
            .filter(
                Vendor.dedup_fingerprint == fingerprint,
                Vendor.deleted_at.is_(None),
                Vendor.merged_into_vendor_id.is_(None),
            )
            .first()
            if existing_same_org is None
            else existing_same_org
        )

        created = False
        vendor: Vendor | None = existing_same_org
        if vendor is None and existing_any is not None and existing_any.organization_id != org_id:
            # Different-org collision — don't mutate someone else's vendor
            out.warnings.append("cross_org_fingerprint_collision")
            out.duplicate_of = existing_any.id
        if vendor is None:
            vendor = Vendor(
                name=name,
                organization_id=org_id,
                status="BASIC",
                is_active=True,
            )
            db.add(vendor)
            db.flush()
            created = True

        # Apply fields
        vendor.name = name
        vendor.legal_name = _trim(row.get("legal_name")) or vendor.legal_name
        vendor.trade_name = _trim(row.get("trade_name")) or vendor.trade_name
        vendor.country = _trim(row.get("country")) or vendor.country
        vendor.region = _trim(row.get("state_province")) or vendor.region
        vendor.website = _trim(row.get("website")) or vendor.website
        if email:
            vendor.contact_email = email
        if phone:
            vendor.contact_phone = phone
        vendor.primary_category_tag = _trim(row.get("primary_category_tag")) or vendor.primary_category_tag
        secondary = _parse_list(row.get("secondary_category_tags"))
        if secondary:
            vendor.secondary_category_tags = secondary
        certs = _parse_list(row.get("certifications"))
        if certs:
            vendor.certifications = certs
        vendor.export_capable = _parse_bool(row.get("export_capable")) or bool(vendor.export_capable)
        vendor.founded_year = _int_or_none(row.get("founded_year")) or vendor.founded_year
        vendor.employee_count_band = _trim(row.get("employee_count_band")) or vendor.employee_count_band
        if row.get("avg_lead_time_days"):
            vendor.avg_lead_time_days = _decimal_or_none(row.get("avg_lead_time_days"))
        if row.get("default_currency"):
            vendor.default_currency = _trim(row.get("default_currency"))[:3].upper()
        vendor.dedup_fingerprint = fingerprint
        vendor.onboarding_method = vendor.onboarding_method or source_of(batch)

        # Location (primary)
        addr = _trim(row.get("address_line1"))
        city = _trim(row.get("city"))
        if addr or city or country_iso2 or _trim(row.get("state_province")):
            self._upsert_primary_location(
                db=db,
                vendor=vendor,
                address_line1=addr,
                address_line2=_trim(row.get("address_line2")) or None,
                city=city or None,
                state_province=_trim(row.get("state_province")) or None,
                postal_code=_trim(row.get("postal_code")) or None,
                country_iso2=country_iso2,
            )

        # Lead-time band (if row provides values)
        moq = _decimal_or_none(row.get("moq"))
        if moq or row.get("avg_lead_time_days") or vendor.primary_category_tag:
            self._upsert_lead_time_band(
                db=db,
                vendor=vendor,
                category_tag=vendor.primary_category_tag,
                moq=moq,
                moq_unit=_trim(row.get("moq_unit")) or None,
                typical_days=_decimal_or_none(row.get("avg_lead_time_days")),
            )

        db.flush()

        # Trust tier + validation (per row — keep cheap)
        try:
            vendor_intelligence_service.validate_vendor_profile(vendor.id, db)
            vendor_intelligence_service.compute_trust_tier(vendor.id, db)
        except Exception:
            logger.exception("trust_tier refresh failed for vendor=%s", vendor.id)
            out.warnings.append("trust_tier_refresh_failed")

        # Duplicate scan (other vendors with same fingerprint)
        dups = vendor_intelligence_service.find_duplicate_candidates(vendor.id, db)
        if dups:
            out.warnings.append(f"duplicate_candidates:{len(dups)}")
            out.duplicate_of = dups[0].candidate_vendor_id

        out.status = "created" if created else "updated"
        out.vendor_id = vendor.id
        self._persist_row(batch, row_idx, row, out, vendor.id, db)
        return out

    # ── Upserts ──────────────────────────────────────────────────────────

    def _upsert_primary_location(
        self,
        db: Session,
        vendor: Vendor,
        address_line1: str | None,
        address_line2: str | None,
        city: str | None,
        state_province: str | None,
        postal_code: str | None,
        country_iso2: str | None,
    ) -> VendorLocation:
        loc = (
            db.query(VendorLocation)
            .filter(
                VendorLocation.vendor_id == vendor.id,
                VendorLocation.is_primary.is_(True),
            )
            .first()
        )
        if loc is None:
            loc = VendorLocation(
                vendor_id=vendor.id,
                label="headquarters",
                is_primary=True,
                is_export_office=bool(vendor.export_capable),
            )
            db.add(loc)
        loc.address_line1 = address_line1 or loc.address_line1
        loc.address_line2 = address_line2 or loc.address_line2
        loc.city = city or loc.city
        loc.state_province = state_province or loc.state_province
        loc.postal_code = postal_code or loc.postal_code
        loc.country_iso2 = country_iso2 or loc.country_iso2
        return loc

    def _upsert_lead_time_band(
        self,
        db: Session,
        vendor: Vendor,
        category_tag: str | None,
        moq: Decimal | None,
        moq_unit: str | None,
        typical_days: Decimal | None,
    ) -> VendorLeadTimeBand:
        band = (
            db.query(VendorLeadTimeBand)
            .filter(
                VendorLeadTimeBand.vendor_id == vendor.id,
                VendorLeadTimeBand.category_tag == category_tag,
            )
            .first()
        )
        if band is None:
            band = VendorLeadTimeBand(
                vendor_id=vendor.id,
                category_tag=category_tag,
                source="self_reported",
                confidence=Decimal("0.70"),
            )
            db.add(band)
        if moq is not None:
            band.moq = moq
        if moq_unit:
            band.moq_unit = moq_unit[:30]
        if typical_days is not None:
            band.lead_time_typical_days = typical_days
        return band

    def _persist_row(
        self,
        batch: VendorImportBatch,
        row_idx: int,
        row: dict[str, Any],
        out: IngestionRowResult,
        vendor_id: str | None,
        db: Session,
    ) -> None:
        # Build idempotency key from batch+row+name
        name = _trim(row.get("name"))
        idem_raw = f"{batch.id}:{row_idx}:{name}"
        idempotency_key = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()[:64]
        db.add(
            VendorImportRow(
                batch_id=batch.id,
                row_index=row_idx,
                status="promoted" if out.status in ("created", "updated") else "failed",
                resolution_status="resolved" if vendor_id else "unresolved",
                idempotency_key=idempotency_key,
                raw_row=dict(row),
                normalized_identity={
                    "name": name,
                    "normalized_name": _trim(name).lower(),
                    "country_iso2": _upper2(row.get("country")),
                    "email": _trim(row.get("contact_email")).lower(),
                },
                parse_errors=[],
                validation_errors=list(out.errors),
                warnings=list(out.warnings),
                source_confidence_tier="self_reported",
                created_vendor_id=vendor_id,
                processed_at=_now(),
            )
        )

    # ── Capabilities CSV ────────────────────────────────────────────────

    def ingest_vendor_capabilities_csv(
        self,
        file_content: bytes | str,
        vendor_id: str,
        db: Session,
    ) -> IngestionResult:
        raw = file_content.decode("utf-8-sig") if isinstance(file_content, bytes) else file_content
        reader = csv.DictReader(io.StringIO(raw))

        batch = VendorImportBatch(
            source_type="capabilities_csv",
            source_ref=vendor_id,
            status="processing",
            import_mode="append",
        )
        db.add(batch)
        db.flush()

        result = IngestionResult(batch_id=batch.id)
        for row_idx, row in enumerate(reader, start=1):
            rr = IngestionRowResult(row_index=row_idx, status="failed")
            result.total_rows += 1

            process = _trim(row.get("process"))
            if not process:
                rr.errors.append("missing_required_field:process")
                result.failed += 1
                result.row_results.append(rr)
                continue

            material_family = _trim(row.get("material_family")) or None
            cap = (
                db.query(VendorCapability)
                .filter(
                    VendorCapability.vendor_id == vendor_id,
                    VendorCapability.process == process,
                    VendorCapability.material_family == material_family,
                )
                .first()
            )
            if cap is None:
                cap = VendorCapability(
                    vendor_id=vendor_id,
                    process=process,
                    material_family=material_family,
                )
                db.add(cap)
                rr.status = "created"
            else:
                rr.status = "updated"

            prof = _decimal_or_none(row.get("proficiency"))
            if prof is not None:
                cap.proficiency = prof
            lt = _decimal_or_none(row.get("typical_lead_days"))
            if lt is not None:
                cap.typical_lead_days = lt
            certs = _parse_list(row.get("certifications"))
            if certs:
                cap.certifications = certs

            # LT band side-effect
            if _decimal_or_none(row.get("lead_time_min_days")) is not None or _decimal_or_none(row.get("lead_time_max_days")) is not None:
                band = (
                    db.query(VendorLeadTimeBand)
                    .filter(
                        VendorLeadTimeBand.vendor_id == vendor_id,
                        VendorLeadTimeBand.category_tag == process,
                    )
                    .first()
                )
                if band is None:
                    band = VendorLeadTimeBand(
                        vendor_id=vendor_id,
                        category_tag=process,
                        material_family=material_family,
                        source="self_reported",
                        confidence=Decimal("0.70"),
                    )
                    db.add(band)
                lmin = _int_or_none(row.get("lead_time_min_days"))
                lmax = _int_or_none(row.get("lead_time_max_days"))
                if lmin is not None:
                    band.lead_time_min_days = lmin
                if lmax is not None:
                    band.lead_time_max_days = lmax
                tm = _decimal_or_none(row.get("lead_time_typical_days"))
                if tm is not None:
                    band.lead_time_typical_days = tm

            result.success += 1
            result.row_results.append(rr)

        # Refresh trust tier after cap changes
        try:
            vendor_intelligence_service.compute_trust_tier(vendor_id, db)
        except Exception:
            logger.exception("trust_tier refresh failed for vendor=%s", vendor_id)

        batch.total_rows = result.total_rows
        batch.processed_rows = result.total_rows
        batch.success_rows = result.success
        batch.failed_rows = result.failed
        batch.status = "complete"
        batch.completed_at = _now()
        db.flush()
        return result


def source_of(batch: VendorImportBatch) -> str:
    """Map batch source_type to Vendor.onboarding_method."""
    st = (batch.source_type or "").lower()
    if "self" in st:
        return "self_onboarded"
    if "buyer" in st or "approved" in st:
        return "buyer_invited"
    return "platform_seeded"


vendor_csv_ingestion_service = VendorCSVIngestionService()
