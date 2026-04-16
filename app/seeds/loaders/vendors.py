from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vendor import (
    Vendor, VendorCapability, VendorLocation, VendorLeadTimeBand,
    VendorTrustTier,
)
from app.seeds.base import (
    SeedError,
    SeedStats,
    ensure_dict,
    ensure_list,
    load_records,
    parse_datetime,
    parse_decimal,
    upsert_table,
)
from app.seeds.tables import vendor_capability_seed_catalog, vendor_seed_catalog
from app.services.vendor_intelligence_service import vendor_intelligence_service

logger = logging.getLogger(__name__)


def load_vendors(seed_root, db: Session) -> list[SeedStats]:
    vendor_rows = load_records(seed_root, "vendors/vendor_catalog.json")
    capability_rows = load_records(seed_root, "vendors/vendor_capabilities.json")

    stats = [
        upsert_table(
            db,
            vendor_seed_catalog,
            [
                {
                    "vendor_id": row["vendor_id"],
                    "name": row["name"],
                    "payload": row,
                    "created_at": parse_datetime(row.get("created_at")),
                    "updated_at": parse_datetime(row.get("updated_at")),
                }
                for row in vendor_rows
            ],
            ["vendor_id"],
            "pricing.vendor_seed_catalog",
        ),
        _sync_operational_vendors(db, vendor_rows),
        upsert_table(
            db,
            vendor_capability_seed_catalog,
            [
                {
                    "capability_id": row["capability_id"],
                    "vendor_id": row["vendor_id"],
                    "taxonomy_code": row.get("taxonomy_code"),
                    "payload": row,
                    "created_at": parse_datetime(row.get("created_at")),
                    "updated_at": parse_datetime(row.get("updated_at")),
                }
                for row in capability_rows
            ],
            ["capability_id"],
            "pricing.vendor_capability_seed_catalog",
        ),
        _sync_operational_capabilities(db, capability_rows, vendor_rows),
    ]

    # ── Phase 3 additive seed loading (all optional; missing files are skipped) ──
    seed_root_path = Path(seed_root)
    stats.append(_sync_vendor_locations_phase3(db, seed_root_path))
    stats.append(_sync_vendor_lead_time_bands_phase3(db, seed_root_path))
    stats.append(_sync_vendor_trust_tiers_phase3(db, seed_root_path))
    stats.append(_refresh_dedup_fingerprints_phase3(db))

    return stats


def _sync_operational_vendors(db: Session, records: list[dict]) -> SeedStats:
    stats = SeedStats(name="pricing.vendors")
    for row in records:
        vendor = db.get(Vendor, row["vendor_id"])
        if vendor is None:
            vendor = Vendor(id=row["vendor_id"])
            stats.inserted += 1
            db.add(vendor)
        else:
            stats.updated += 1

        metadata = dict(vendor.metadata_ or {})
        metadata.update(
            {
                "vendor_type": row.get("vendor_type"),
                "company_size": row.get("company_size"),
                "country_code": row.get("country_code"),
                "city": row.get("city"),
                "address": ensure_dict(row.get("address")),
                "accepted_currencies": ensure_list(row.get("accepted_currencies")),
                "incoterms_supported": ensure_list(row.get("incoterms_supported")),
                "payment_terms_options": ensure_list(row.get("payment_terms_options")),
                "regions_served_macro": ensure_list(row.get("regions_served_macro")),
                "trading_name": row.get("trading_name"),
                "year_established": row.get("year_established"),
                "seed_source": "phase1_seed_assets",
            }
        )

        vendor.name = row["name"]
        vendor.legal_name = row.get("legal_name")
        vendor.country = row.get("country")
        vendor.region = row.get("region")
        vendor.website = row.get("website")
        vendor.contact_email = row.get("contact_email")
        vendor.contact_phone = row.get("phone")
        vendor.reliability_score = parse_decimal(row.get("reliability_score")) or Decimal("0.8")
        vendor.avg_lead_time_days = parse_decimal(row.get("avg_lead_time_days"))
        vendor.default_currency = row.get("default_currency") or "USD"
        vendor.default_moq = parse_decimal(row.get("default_moq"))
        vendor.regions_served = ensure_list(row.get("regions_served"))
        vendor.certifications = ensure_list(row.get("certifications"))
        vendor.capacity_profile = ensure_dict(row.get("capacity_profile"))
        vendor.quality_rating = parse_decimal(row.get("quality_rating")) or Decimal("0")
        vendor.is_active = bool(row.get("is_active", True))
        vendor.metadata_ = metadata
        vendor.status = row.get("status") or "BASIC"
        vendor.profile_completeness = int(row.get("profile_completeness") or 100)
        vendor.identity_json = ensure_dict(row.get("identity_json"))
        vendor.commercial_terms_json = ensure_dict(row.get("commercial_terms_json"))
        vendor.lead_time_profile_json = ensure_dict(row.get("lead_time_profile_json"))
        vendor.onboarding_method = row.get("onboarding_method")
        vendor.created_at = parse_datetime(row.get("created_at")) or vendor.created_at
        vendor.updated_at = parse_datetime(row.get("updated_at")) or vendor.updated_at
        vendor.deleted_at = parse_datetime(row.get("deleted_at"))

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _sync_operational_capabilities(db: Session, capability_rows: list[dict], vendor_rows: list[dict]) -> SeedStats:
    stats = SeedStats(name="pricing.vendor_capabilities")
    vendor_by_id = {row["vendor_id"]: row for row in vendor_rows}

    for row in capability_rows:
        vendor_row = vendor_by_id.get(row["vendor_id"], {})
        certifications = ensure_list(vendor_row.get("certifications"))
        material_types = ensure_list(row.get("material_types")) or [None]
        processes = ensure_list(row.get("manufacturing_processes")) or [row.get("taxonomy_code") or "unknown"]

        proficiency = Decimal("0.95") if row.get("is_verified") else Decimal("0.75")
        lead_days = parse_decimal(ensure_dict(row.get("capacity_profile")).get("preferred_batch_size"))
        if lead_days is None:
            lead_days = parse_decimal(vendor_row.get("avg_lead_time_days"))

        for process in processes:
            for material in material_types:
                existing = db.execute(
                    select(VendorCapability).where(
                        VendorCapability.vendor_id == row["vendor_id"],
                        VendorCapability.process == process,
                        VendorCapability.material_family == material,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    existing = VendorCapability(
                        vendor_id=row["vendor_id"],
                        process=process,
                        material_family=material,
                    )
                    db.add(existing)
                    stats.inserted += 1
                else:
                    stats.updated += 1

                existing.proficiency = proficiency
                existing.typical_lead_days = lead_days
                existing.certifications = certifications
                existing.is_active = True
                existing.created_at = parse_datetime(row.get("created_at")) or existing.created_at

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — additive vendor-intelligence seed sync
# ═════════════════════════════════════════════════════════════════════════════


def _read_phase3_seed(seed_root: Path, relative_path: str) -> list[dict]:
    """Read a Phase-3 seed file if present, else return []."""
    try:
        return load_records(seed_root, relative_path)
    except SeedError:
        logger.info("phase3 seed not present: %s (skipping)", relative_path)
        return []


def _sync_vendor_locations_phase3(db: Session, seed_root: Path) -> SeedStats:
    stats = SeedStats(name="pricing.vendor_locations")
    rows = _read_phase3_seed(seed_root, "vendors/vendor_locations.json")
    for row in rows:
        vendor_id = row.get("vendor_id")
        if not vendor_id:
            continue
        if db.get(Vendor, vendor_id) is None:
            continue
        loc = db.execute(
            select(VendorLocation).where(
                VendorLocation.vendor_id == vendor_id,
                VendorLocation.is_primary == bool(row.get("is_primary", False)),
                VendorLocation.label == row.get("label"),
            )
        ).scalar_one_or_none()
        if loc is None:
            loc = VendorLocation(vendor_id=vendor_id)
            db.add(loc)
            stats.inserted += 1
        else:
            stats.updated += 1
        loc.label = row.get("label") or loc.label
        loc.address_line1 = row.get("address_line1") or loc.address_line1
        loc.address_line2 = row.get("address_line2") or loc.address_line2
        loc.city = row.get("city") or loc.city
        loc.state_province = row.get("state_province") or loc.state_province
        loc.postal_code = row.get("postal_code") or loc.postal_code
        loc.country_iso2 = row.get("country_iso2") or loc.country_iso2
        loc.latitude = parse_decimal(row.get("latitude"))
        loc.longitude = parse_decimal(row.get("longitude"))
        loc.geo_region_tag = row.get("geo_region_tag") or loc.geo_region_tag
        loc.is_primary = bool(row.get("is_primary", loc.is_primary))
        loc.is_export_office = bool(row.get("is_export_office", loc.is_export_office))
    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _sync_vendor_lead_time_bands_phase3(db: Session, seed_root: Path) -> SeedStats:
    stats = SeedStats(name="pricing.vendor_lead_time_bands")
    rows = _read_phase3_seed(seed_root, "vendors/vendor_lead_time_bands.json")
    for row in rows:
        vendor_id = row.get("vendor_id")
        if not vendor_id or db.get(Vendor, vendor_id) is None:
            continue
        band = db.execute(
            select(VendorLeadTimeBand).where(
                VendorLeadTimeBand.vendor_id == vendor_id,
                VendorLeadTimeBand.category_tag == row.get("category_tag"),
                VendorLeadTimeBand.material_family == row.get("material_family"),
            )
        ).scalar_one_or_none()
        if band is None:
            band = VendorLeadTimeBand(
                vendor_id=vendor_id,
                category_tag=row.get("category_tag"),
                material_family=row.get("material_family"),
            )
            db.add(band)
            stats.inserted += 1
        else:
            stats.updated += 1
        band.moq = parse_decimal(row.get("moq"))
        band.moq_unit = row.get("moq_unit") or band.moq_unit
        band.lead_time_min_days = row.get("lead_time_min_days") or band.lead_time_min_days
        band.lead_time_max_days = row.get("lead_time_max_days") or band.lead_time_max_days
        band.lead_time_typical_days = parse_decimal(row.get("lead_time_typical_days"))
        band.confidence = parse_decimal(row.get("confidence")) or Decimal("0.5")
        band.source = row.get("source") or "self_reported"
    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _sync_vendor_trust_tiers_phase3(db: Session, seed_root: Path) -> SeedStats:
    stats = SeedStats(name="pricing.vendor_trust_tiers")
    rows = _read_phase3_seed(seed_root, "vendors/vendor_trust_tiers.json")
    for row in rows:
        vendor_id = row.get("vendor_id")
        if not vendor_id:
            continue
        vendor = db.get(Vendor, vendor_id)
        if vendor is None:
            continue
        record = db.execute(
            select(VendorTrustTier).where(VendorTrustTier.vendor_id == vendor_id)
        ).scalar_one_or_none()
        if record is None:
            record = VendorTrustTier(vendor_id=vendor_id)
            db.add(record)
            stats.inserted += 1
        else:
            stats.updated += 1
        record.tier = row.get("tier") or "UNVERIFIED"
        record.data_completeness_score = parse_decimal(row.get("data_completeness_score")) or Decimal("0")
        record.reliability_score = parse_decimal(row.get("reliability_score")) or Decimal("0")
        record.evidence_count = int(row.get("evidence_count") or 0)
        record.missing_required_fields = list(row.get("missing_required_fields") or [])
        record.flags = list(row.get("flags") or [])
        # Denormalize onto vendor row
        vendor.trust_tier = record.tier
        vendor.missing_required_fields = list(record.missing_required_fields or [])
        vendor.profile_flags = list(record.flags or [])
    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _refresh_dedup_fingerprints_phase3(db: Session) -> SeedStats:
    stats = SeedStats(name="pricing.vendors.dedup_fingerprint")
    vendors = db.execute(
        select(Vendor).where(
            Vendor.is_active.is_(True),
            Vendor.deleted_at.is_(None),
            Vendor.merged_into_vendor_id.is_(None),
        )
    ).scalars().all()
    for v in vendors:
        vendor_intelligence_service.refresh_vendor_fingerprint(db, v)
        stats.updated += 1
    logger.info("refreshed %s | updated=%s", stats.name, stats.updated)
    return stats
