"""
Vendor Intelligence Service.

Implements Execution Plan §1 (Vendor Intelligence Model) and §8 (Data
Ingestion — deduplication / validation / automated quality checks).

Capabilities:
  - compute_trust_tier: data completeness + reliability → tier label
  - validate_vendor_profile: required-field + address sanity checks
  - compute_dedup_fingerprint: fuzzy-hash for duplicate grouping
  - find_duplicate_candidates: find vendors with same fingerprint /
    high trigram similarity
  - merge_vendor_duplicates: consolidate duplicate into primary
  - run_batch_validation_and_dedup: scheduled sweep
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.vendor import (
    Vendor, VendorCapability, VendorLocation, VendorLeadTimeBand,
    VendorIdentityAlias, VendorEvidenceAttachment,
    VendorCommunicationScore, VendorTrustTier,
    VendorPerformanceSnapshot, VendorImportRow, VendorMatch,
)

logger = logging.getLogger(__name__)


TIER_PLATINUM = "PLATINUM"
TIER_GOLD = "GOLD"
TIER_SILVER = "SILVER"
TIER_BRONZE = "BRONZE"
TIER_UNVERIFIED = "UNVERIFIED"

REQUIRED_FIELDS = (
    "name",
    "primary_location",
    "primary_category_tag",
    "lead_time_band",
    "contact",
)

OPTIONAL_FIELDS_WEIGHTS = {
    # field_key -> weight contribution to completeness (0..1)
    "legal_name": 0.05,
    "website": 0.05,
    "trade_name": 0.03,
    "founded_year": 0.03,
    "employee_count_band": 0.04,
    "certifications": 0.07,
    "secondary_category_tags": 0.05,
    "export_capable": 0.04,
    "communication_score": 0.04,
}

# Legal-suffix noise stripped when computing dedup fingerprints and fuzzy match.
_LEGAL_SUFFIXES = (
    "pvt ltd", "private limited", "private ltd",
    "pvt. ltd.", "pvt. ltd", "pvt ltd.", "pvt. limited",
    "ltd", "ltd.", "limited",
    "inc", "inc.", "incorporated",
    "co", "co.", "company",
    "corp", "corp.", "corporation",
    "llc", "l.l.c.",
    "llp", "l.l.p.",
    "gmbh", "gmbh.", "ag",
    "sa", "s.a.", "sas", "s.a.s.",
    "bv", "b.v.",
    "plc",
    "pty ltd", "pty. ltd.", "pty",
    "srl", "s.r.l.",
)


@dataclass
class TrustTierResult:
    tier: str
    data_completeness_score: float
    reliability_score: float
    evidence_count: int
    missing_required_fields: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "data_completeness_score": round(self.data_completeness_score, 4),
            "reliability_score": round(self.reliability_score, 4),
            "evidence_count": self.evidence_count,
            "missing_required_fields": list(self.missing_required_fields),
            "flags": list(self.flags),
        }


@dataclass
class ValidationResult:
    vendor_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validated_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class DuplicateCandidate:
    primary_vendor_id: str
    candidate_vendor_id: str
    candidate_name: str
    similarity: float
    match_reason: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    # Lowercase, strip punctuation, collapse whitespace.
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Remove trailing legal suffixes (can be repeated).
    changed = True
    while changed:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            if cleaned.endswith(" " + suffix):
                cleaned = cleaned[: -len(suffix) - 1].strip()
                changed = True
            elif cleaned == suffix:
                cleaned = ""
                changed = True
    return cleaned


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


class VendorIntelligenceService:
    """Service for vendor trust-tier, validation, and deduplication."""

    # ── Fingerprints and duplicate detection ──────────────────────────────

    def compute_dedup_fingerprint(self, vendor: dict[str, Any]) -> str:
        """
        Compute a deterministic fuzzy-hash fingerprint for duplicate
        detection. Inputs considered: normalized name, country ISO2,
        and contact-email domain.
        """
        name = _normalize_name(str(vendor.get("name") or vendor.get("legal_name") or ""))
        country = str(vendor.get("country_iso2") or vendor.get("country") or "").strip().lower()[:2]
        email_dom = _email_domain(vendor.get("contact_email"))
        canonical = f"{name}|{country}|{email_dom}"
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return digest[:32]

    def _vendor_as_dict_for_fingerprint(self, vendor: Vendor) -> dict[str, Any]:
        country = vendor.country
        # Prefer first location country if available
        if vendor.locations:
            primary_loc = next(
                (loc for loc in vendor.locations if loc.is_primary),
                vendor.locations[0],
            )
            country = primary_loc.country_iso2 or country
        return {
            "name": vendor.name,
            "legal_name": vendor.legal_name,
            "country_iso2": country,
            "contact_email": vendor.contact_email,
        }

    def refresh_vendor_fingerprint(self, db: Session, vendor: Vendor) -> str:
        fp = self.compute_dedup_fingerprint(self._vendor_as_dict_for_fingerprint(vendor))
        vendor.dedup_fingerprint = fp
        return fp

    def find_duplicate_candidates(
        self,
        vendor_id: str,
        db: Session,
        min_similarity: float = 0.85,
    ) -> list[DuplicateCandidate]:
        """Find potential duplicates for a vendor using fingerprint + fuzzy name match."""
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            return []

        fp = vendor.dedup_fingerprint or self.refresh_vendor_fingerprint(db, vendor)

        # Exact fingerprint match
        fp_hits = (
            db.query(Vendor)
            .filter(
                Vendor.dedup_fingerprint == fp,
                Vendor.id != vendor.id,
                Vendor.deleted_at.is_(None),
                Vendor.merged_into_vendor_id.is_(None),
            )
            .all()
        )
        candidates: dict[str, DuplicateCandidate] = {}
        for other in fp_hits:
            candidates[other.id] = DuplicateCandidate(
                primary_vendor_id=vendor.id,
                candidate_vendor_id=other.id,
                candidate_name=other.name,
                similarity=1.0,
                match_reason="fingerprint_exact",
            )

        # Fuzzy name similarity over candidate pool (same country when known)
        normalized_self = _normalize_name(vendor.name)
        if normalized_self:
            pool_query = db.query(Vendor).filter(
                Vendor.id != vendor.id,
                Vendor.deleted_at.is_(None),
                Vendor.merged_into_vendor_id.is_(None),
            )
            if vendor.country:
                pool_query = pool_query.filter(
                    (Vendor.country == vendor.country) | (Vendor.country.is_(None))
                )
            for other in pool_query.limit(500).all():
                if other.id in candidates:
                    continue
                normalized_other = _normalize_name(other.name)
                if not normalized_other:
                    continue
                sim = difflib.SequenceMatcher(
                    None, normalized_self, normalized_other
                ).ratio()
                if sim >= min_similarity:
                    candidates[other.id] = DuplicateCandidate(
                        primary_vendor_id=vendor.id,
                        candidate_vendor_id=other.id,
                        candidate_name=other.name,
                        similarity=round(sim, 4),
                        match_reason="fuzzy_name",
                    )

        return sorted(
            candidates.values(), key=lambda c: c.similarity, reverse=True
        )

    # ── Validation ────────────────────────────────────────────────────────

    def validate_vendor_profile(
        self,
        vendor_id: str,
        db: Session,
    ) -> ValidationResult:
        """Per-vendor profile validation; persists validation_errors + last_validated_at."""
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            return ValidationResult(vendor_id=vendor_id, errors=["vendor_not_found"])

        result = ValidationResult(vendor_id=vendor.id)

        if not _non_empty(vendor.name):
            result.errors.append("missing_name")

        email = (vendor.contact_email or "").strip()
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            result.errors.append("malformed_contact_email")

        if not vendor.primary_category_tag:
            result.warnings.append("missing_primary_category_tag")

        # At least one location with country_iso2
        country_ok = False
        if vendor.locations:
            for loc in vendor.locations:
                if _non_empty(loc.country_iso2):
                    country_ok = True
                else:
                    result.warnings.append(
                        f"location_missing_country:{loc.label or loc.id}"
                    )
        elif _non_empty(vendor.country):
            country_ok = True
        else:
            result.errors.append("no_locations_on_record")
        if not country_ok:
            result.errors.append("no_country_on_any_location")

        # Lead time band presence
        bands_exist = (
            db.query(VendorLeadTimeBand.id)
            .filter(VendorLeadTimeBand.vendor_id == vendor.id)
            .first()
            is not None
            or vendor.avg_lead_time_days is not None
        )
        if not bands_exist:
            result.errors.append("no_lead_time_band")

        # Contact info
        if not (_non_empty(vendor.contact_email) or _non_empty(vendor.contact_phone)):
            result.errors.append("no_contact_info")

        vendor.validation_errors = list(result.errors)
        vendor.last_validated_at = _now()
        vendor.address_validated = country_ok
        result.validated_at = vendor.last_validated_at
        return result

    # ── Trust tier ────────────────────────────────────────────────────────

    def _has_required_fields(self, vendor: Vendor, db: Session) -> tuple[list[str], int]:
        """Return (missing_required_fields, required_present_count)."""
        missing: list[str] = []

        if not _non_empty(vendor.name):
            missing.append("name")

        has_primary_loc = any(
            _non_empty(loc.country_iso2) for loc in (vendor.locations or [])
        )
        if not has_primary_loc and not _non_empty(vendor.country):
            missing.append("primary_location")

        has_category = _non_empty(vendor.primary_category_tag) or _non_empty(
            vendor.secondary_category_tags
        )
        # Also accept category tag from vendor_capabilities
        if not has_category:
            cap_count = (
                db.query(VendorCapability.id)
                .filter(
                    VendorCapability.vendor_id == vendor.id,
                    VendorCapability.is_active.is_(True),
                )
                .count()
            )
            has_category = cap_count > 0
        if not has_category:
            missing.append("primary_category_tag")

        has_lt_band = (
            db.query(VendorLeadTimeBand.id)
            .filter(VendorLeadTimeBand.vendor_id == vendor.id)
            .first()
            is not None
            or vendor.avg_lead_time_days is not None
        )
        if not has_lt_band:
            missing.append("lead_time_band")

        if not (_non_empty(vendor.contact_email) or _non_empty(vendor.contact_phone)):
            missing.append("contact")

        present_count = len(REQUIRED_FIELDS) - len(missing)
        return missing, present_count

    def _completeness_score(
        self,
        vendor: Vendor,
        missing: list[str],
        present_required: int,
    ) -> float:
        required_fraction = present_required / max(1, len(REQUIRED_FIELDS))
        # Required fields dominate 0.70 of the score; optional fills the rest
        optional_contrib = 0.0
        present_optional_weight = 0.0
        total_optional_weight = sum(OPTIONAL_FIELDS_WEIGHTS.values()) or 1.0
        for key, weight in OPTIONAL_FIELDS_WEIGHTS.items():
            value = getattr(vendor, key, None)
            if _non_empty(value):
                present_optional_weight += weight
        optional_contrib = present_optional_weight / total_optional_weight
        completeness = (0.70 * required_fraction) + (0.30 * optional_contrib)
        return max(0.0, min(1.0, completeness))

    def _reliability_score(self, vendor: Vendor, db: Session) -> tuple[float, int]:
        """Reliability from latest performance snapshot + base reliability_score."""
        latest = (
            db.query(VendorPerformanceSnapshot)
            .filter(VendorPerformanceSnapshot.vendor_id == vendor.id)
            .order_by(VendorPerformanceSnapshot.snapshot_date.desc())
            .first()
        )
        base = _as_float(vendor.reliability_score, 0.5)
        if not latest or not latest.total_pos:
            return base, 0
        on_time = _as_float(latest.on_time_delivery_pct, base)
        quality = _as_float(latest.quality_pass_pct, base)
        reliability = 0.60 * on_time + 0.40 * quality
        return max(0.0, min(1.0, reliability)), int(latest.total_pos or 0)

    def compute_trust_tier(
        self,
        vendor_id: str,
        db: Session,
    ) -> TrustTierResult:
        """
        Compute trust tier based on data completeness + reliability.

        PLATINUM: completeness ≥ 0.90 AND reliability ≥ 0.90
        GOLD:     completeness ≥ 0.75 AND reliability ≥ 0.75
        SILVER:   completeness ≥ 0.60 AND reliability ≥ 0.60
        BRONZE:   completeness ≥ 0.40
        UNVERIFIED: below 0.40 OR required fields missing
        """
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            return TrustTierResult(
                tier=TIER_UNVERIFIED,
                data_completeness_score=0.0,
                reliability_score=0.0,
                evidence_count=0,
                missing_required_fields=list(REQUIRED_FIELDS),
                flags=["vendor_not_found"],
            )

        missing, present_required = self._has_required_fields(vendor, db)
        completeness = self._completeness_score(vendor, missing, present_required)
        reliability, evidence_count = self._reliability_score(vendor, db)

        flags: list[str] = []
        if missing:
            flags.append("incomplete_profile")
        if evidence_count == 0:
            flags.append("no_performance_history")
        if vendor.last_validated_at is None:
            flags.append("never_validated")
        elif (_now() - (
            vendor.last_validated_at
            if vendor.last_validated_at.tzinfo
            else vendor.last_validated_at.replace(tzinfo=timezone.utc)
        )).days > 90:
            flags.append("stale_data")

        # Tier assignment
        if missing or completeness < 0.40:
            tier = TIER_UNVERIFIED
        elif completeness >= 0.90 and reliability >= 0.90:
            tier = TIER_PLATINUM
        elif completeness >= 0.75 and reliability >= 0.75:
            tier = TIER_GOLD
        elif completeness >= 0.60 and reliability >= 0.60:
            tier = TIER_SILVER
        else:
            tier = TIER_BRONZE

        result = TrustTierResult(
            tier=tier,
            data_completeness_score=completeness,
            reliability_score=reliability,
            evidence_count=evidence_count,
            missing_required_fields=missing,
            flags=flags,
        )

        # Persist to vendor_trust_tiers + denormalized vendor.trust_tier
        record = vendor.trust_tier_record
        if record is None:
            record = VendorTrustTier(vendor_id=vendor.id)
            db.add(record)
        record.tier = result.tier
        record.data_completeness_score = Decimal(str(round(completeness, 4)))
        record.reliability_score = Decimal(str(round(reliability, 4)))
        record.evidence_count = evidence_count
        record.missing_required_fields = list(missing)
        record.flags = list(flags)
        record.computed_at = _now()

        vendor.trust_tier = tier
        vendor.missing_required_fields = list(missing)
        vendor.profile_flags = list(flags)

        logger.info(
            "trust_tier vendor_id=%s tier=%s completeness=%.4f reliability=%.4f evidence=%d",
            vendor.id, tier, completeness, reliability, evidence_count,
        )
        return result

    # ── Merging ──────────────────────────────────────────────────────────

    def merge_vendor_duplicates(
        self,
        primary_id: str,
        duplicate_id: str,
        db: Session,
    ) -> dict[str, Any]:
        """Consolidate duplicate vendor into primary. Append-moves child rows."""
        if primary_id == duplicate_id:
            raise ValueError("primary_id and duplicate_id must differ")

        primary = db.query(Vendor).filter(Vendor.id == primary_id).first()
        duplicate = db.query(Vendor).filter(Vendor.id == duplicate_id).first()
        if not primary or not duplicate:
            raise ValueError("primary or duplicate vendor not found")

        moved = {
            "capabilities": 0,
            "locations": 0,
            "lead_time_bands": 0,
            "export_capabilities": 0,
            "identity_aliases": 0,
            "evidence_attachments": 0,
        }

        # Capabilities (avoid exact duplicates)
        existing_caps = {
            (c.process, c.material_family)
            for c in db.query(VendorCapability)
            .filter(VendorCapability.vendor_id == primary.id)
            .all()
        }
        for cap in list(
            db.query(VendorCapability)
            .filter(VendorCapability.vendor_id == duplicate.id)
            .all()
        ):
            key = (cap.process, cap.material_family)
            if key in existing_caps:
                db.delete(cap)
            else:
                cap.vendor_id = primary.id
                moved["capabilities"] += 1
                existing_caps.add(key)

        # Locations
        for loc in list(
            db.query(VendorLocation)
            .filter(VendorLocation.vendor_id == duplicate.id)
            .all()
        ):
            loc.vendor_id = primary.id
            loc.is_primary = False  # primary already has a primary
            moved["locations"] += 1

        # Lead-time bands
        for band in list(
            db.query(VendorLeadTimeBand)
            .filter(VendorLeadTimeBand.vendor_id == duplicate.id)
            .all()
        ):
            band.vendor_id = primary.id
            moved["lead_time_bands"] += 1

        # Export capabilities
        from app.models.vendor import VendorExportCapability as _VEC
        for xcap in list(
            db.query(_VEC).filter(_VEC.vendor_id == duplicate.id).all()
        ):
            xcap.vendor_id = primary.id
            moved["export_capabilities"] += 1

        # Identity aliases (preserve dup's name as an alias of primary)
        for alias in list(
            db.query(VendorIdentityAlias)
            .filter(VendorIdentityAlias.vendor_id == duplicate.id)
            .all()
        ):
            alias.vendor_id = primary.id
            moved["identity_aliases"] += 1

        # Record the duplicate's name as an alias of the primary if new
        dup_alias_norm = _normalize_name(duplicate.name)
        if dup_alias_norm:
            already = (
                db.query(VendorIdentityAlias)
                .filter(
                    VendorIdentityAlias.vendor_id == primary.id,
                    VendorIdentityAlias.alias_type == "trade_name",
                    VendorIdentityAlias.normalized_value == dup_alias_norm,
                )
                .first()
            )
            if already is None:
                db.add(
                    VendorIdentityAlias(
                        vendor_id=primary.id,
                        alias_type="trade_name",
                        alias_value=duplicate.name,
                        normalized_value=dup_alias_norm,
                        confidence=Decimal("0.90"),
                        provenance="merge",
                    )
                )

        # Evidence attachments
        for ev in list(
            db.query(VendorEvidenceAttachment)
            .filter(VendorEvidenceAttachment.vendor_id == duplicate.id)
            .all()
        ):
            ev.vendor_id = primary.id
            moved["evidence_attachments"] += 1

        # Update import rows pointing at duplicate
        for row in (
            db.query(VendorImportRow)
            .filter(
                (VendorImportRow.matched_vendor_id == duplicate.id)
                | (VendorImportRow.created_vendor_id == duplicate.id)
            )
            .all()
        ):
            if row.matched_vendor_id == duplicate.id:
                row.matched_vendor_id = primary.id
            if row.created_vendor_id == duplicate.id:
                row.created_vendor_id = primary.id

        # Vendor match history: reassign so history isn't lost
        for vm in (
            db.query(VendorMatch).filter(VendorMatch.vendor_id == duplicate.id).all()
        ):
            vm.vendor_id = primary.id

        duplicate.merged_into_vendor_id = primary.id
        duplicate.is_active = False
        duplicate.deleted_at = _now()

        db.flush()

        # Recompute tier for primary after consolidation
        self.compute_trust_tier(primary.id, db)

        logger.info(
            "merge_vendor_duplicates primary=%s duplicate=%s moved=%s",
            primary.id, duplicate.id, moved,
        )
        return {
            "primary_vendor_id": primary.id,
            "duplicate_vendor_id": duplicate.id,
            "moved": moved,
        }

    # ── Batch ────────────────────────────────────────────────────────────

    def run_batch_validation_and_dedup(
        self,
        db: Session,
        org_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Scheduled automated data-quality sweep.

        For each active vendor: refresh fingerprint, validate profile,
        recompute trust tier, report duplicate candidates.
        """
        q = db.query(Vendor).filter(
            Vendor.is_active.is_(True),
            Vendor.deleted_at.is_(None),
            Vendor.merged_into_vendor_id.is_(None),
        )
        if org_id is not None:
            q = q.filter(Vendor.organization_id == org_id)
        if limit is not None:
            q = q.limit(int(limit))

        total = 0
        validated = 0
        invalid = 0
        tier_counts: dict[str, int] = {}
        dup_pairs: list[dict[str, Any]] = []

        for vendor in q.all():
            total += 1
            self.refresh_vendor_fingerprint(db, vendor)
            vr = self.validate_vendor_profile(vendor.id, db)
            if vr.ok:
                validated += 1
            else:
                invalid += 1
            tier = self.compute_trust_tier(vendor.id, db).tier
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

            dups = self.find_duplicate_candidates(vendor.id, db)
            for d in dups:
                # Only report each ordered pair once (lower id first)
                a, b = sorted([d.primary_vendor_id, d.candidate_vendor_id])
                dup_pairs.append(
                    {
                        "vendor_a": a,
                        "vendor_b": b,
                        "similarity": d.similarity,
                        "reason": d.match_reason,
                    }
                )

        # Deduplicate pair list
        seen: set[tuple[str, str]] = set()
        unique_dups: list[dict[str, Any]] = []
        for pair in dup_pairs:
            key = (pair["vendor_a"], pair["vendor_b"])
            if key in seen:
                continue
            seen.add(key)
            unique_dups.append(pair)

        summary = {
            "total_vendors_checked": total,
            "validated_ok": validated,
            "validation_errors": invalid,
            "tier_counts": tier_counts,
            "duplicate_candidate_pairs": unique_dups,
            "ran_at": _now().isoformat(),
        }
        logger.info("run_batch_validation_and_dedup summary=%s", summary)
        return summary


vendor_intelligence_service = VendorIntelligenceService()
