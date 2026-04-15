from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.vendor import Vendor, VendorIdentityAlias, VendorImportBatch, VendorImportRow


_ALIAS_PRIORITY = {
    "registration_number": Decimal("0.99"),
    "external_source_id": Decimal("0.96"),
    "domain": Decimal("0.93"),
    "lei": Decimal("0.93"),
    "legal_name": Decimal("0.86"),
}


@dataclass
class ResolutionCandidate:
    vendor: Vendor
    confidence: Decimal
    match_type: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VendorResolutionResult:
    status: str
    confidence: Decimal
    vendor: Vendor | None = None
    candidates: list[ResolutionCandidate] = field(default_factory=list)
    normalized_identity: dict[str, str] = field(default_factory=dict)
    aliases_to_attach: list[dict[str, Any]] = field(default_factory=list)
    collision_key: str | None = None


class VendorResolutionService:
    SAFE_ALIAS_TYPES = {"domain", "legal_name", "registration_number", "lei", "external_source_id"}

    def normalize_identity(self, raw: dict[str, Any]) -> dict[str, str]:
        identity: dict[str, str] = {}

        legal_name = self._normalize_name(raw.get("legal_name") or raw.get("name"))
        if legal_name:
            identity["legal_name"] = legal_name

        website = str(raw.get("website") or "").strip()
        domain = self._normalize_domain(raw.get("domain") or website or raw.get("contact_email"))
        if domain:
            identity["domain"] = domain

        reg = self._normalize_identifier(raw.get("registration_number") or raw.get("tax_id"))
        if reg:
            identity["registration_number"] = reg

        lei = self._normalize_identifier(raw.get("lei"))
        if lei:
            identity["lei"] = lei

        ext = self._normalize_external_source_id(raw.get("external_source_id"), raw.get("source_type"))
        if ext:
            identity["external_source_id"] = ext

        return identity

    def resolve_vendor(self, db: Session, *, raw_identity: dict[str, Any]) -> VendorResolutionResult:
        normalized = self.normalize_identity(raw_identity)
        if not normalized:
            return VendorResolutionResult(
                status="new_vendor",
                confidence=Decimal("0"),
                normalized_identity={},
                collision_key=None,
            )

        candidates: dict[str, ResolutionCandidate] = {}

        aliases = (
            db.query(VendorIdentityAlias)
            .filter(
                VendorIdentityAlias.is_active.is_(True),
                or_(
                    *[
                        (
                            (VendorIdentityAlias.alias_type == alias_type)
                            & (VendorIdentityAlias.normalized_value == value)
                        )
                        for alias_type, value in normalized.items()
                    ]
                ),
            )
            .all()
        )
        for alias in aliases:
            vendor = db.query(Vendor).filter(Vendor.id == alias.vendor_id, Vendor.deleted_at.is_(None)).first()
            if not vendor:
                continue
            bucket = candidates.get(vendor.id)
            score = min(Decimal("1.0"), Decimal(str(alias.confidence or 0)) or _ALIAS_PRIORITY.get(alias.alias_type, Decimal("0.75")))
            evidence = {
                "alias_type": alias.alias_type,
                "normalized_value": alias.normalized_value,
                "provenance": alias.provenance,
                "source_ref": alias.source_ref,
            }
            if bucket is None:
                candidates[vendor.id] = ResolutionCandidate(
                    vendor=vendor,
                    confidence=score,
                    match_type="alias",
                    evidence=[evidence],
                )
            else:
                bucket.confidence = max(bucket.confidence, score)
                bucket.evidence.append(evidence)

        if normalized.get("legal_name"):
            name_hits = (
                db.query(Vendor)
                .filter(
                    Vendor.deleted_at.is_(None),
                    or_(
                        func.lower(Vendor.legal_name) == normalized["legal_name"],
                        func.lower(Vendor.name) == normalized["legal_name"],
                    ),
                )
                .all()
            )
            for vendor in name_hits:
                bucket = candidates.get(vendor.id)
                evidence = {"alias_type": "legal_name", "normalized_value": normalized["legal_name"], "provenance": "vendor_table"}
                if bucket is None:
                    candidates[vendor.id] = ResolutionCandidate(
                        vendor=vendor,
                        confidence=_ALIAS_PRIORITY["legal_name"],
                        match_type="name",
                        evidence=[evidence],
                    )
                else:
                    bucket.confidence = max(bucket.confidence, _ALIAS_PRIORITY["legal_name"])
                    bucket.evidence.append(evidence)

        ordered = sorted(candidates.values(), key=lambda item: (item.confidence, item.vendor.created_at), reverse=True)
        if not ordered:
            return VendorResolutionResult(
                status="new_vendor",
                confidence=Decimal("0"),
                normalized_identity=normalized,
                aliases_to_attach=self._aliases_from_normalized(normalized),
                collision_key=self._collision_key(normalized),
            )

        best = ordered[0]
        second = ordered[1] if len(ordered) > 1 else None
        if best.confidence >= Decimal("0.95") and (second is None or best.confidence - second.confidence >= Decimal("0.03")):
            return VendorResolutionResult(
                status="strong_match",
                confidence=best.confidence,
                vendor=best.vendor,
                candidates=ordered,
                normalized_identity=normalized,
                aliases_to_attach=self._missing_aliases(db, best.vendor, normalized),
                collision_key=self._collision_key(normalized),
            )

        return VendorResolutionResult(
            status="weak_match",
            confidence=best.confidence,
            vendor=best.vendor,
            candidates=ordered,
            normalized_identity=normalized,
            aliases_to_attach=[],
            collision_key=self._collision_key(normalized),
        )

    def attach_aliases(
        self,
        db: Session,
        *,
        vendor: Vendor,
        aliases: list[dict[str, Any]],
        source_batch: VendorImportBatch | None = None,
        source_row: VendorImportRow | None = None,
        provenance: str = "import",
        source_ref: str | None = None,
    ) -> list[VendorIdentityAlias]:
        attached: list[VendorIdentityAlias] = []
        for alias in aliases:
            alias_type = alias["alias_type"]
            normalized_value = alias["normalized_value"]
            existing = (
                db.query(VendorIdentityAlias)
                .filter(
                    VendorIdentityAlias.vendor_id == vendor.id,
                    VendorIdentityAlias.alias_type == alias_type,
                    VendorIdentityAlias.normalized_value == normalized_value,
                )
                .first()
            )
            if existing:
                existing.confidence = max(Decimal(str(existing.confidence or 0)), Decimal(str(alias.get("confidence", 0))))
                existing.provenance = provenance or existing.provenance
                existing.source_ref = source_ref or existing.source_ref
                if source_batch is not None:
                    existing.source_batch_id = source_batch.id
                if source_row is not None:
                    existing.source_row_id = source_row.id
                existing.source_metadata = {**(existing.source_metadata or {}), **(alias.get("source_metadata") or {})}
                attached.append(existing)
                continue

            row = VendorIdentityAlias(
                vendor_id=vendor.id,
                alias_type=alias_type,
                alias_value=alias["alias_value"],
                normalized_value=normalized_value,
                confidence=alias.get("confidence", _ALIAS_PRIORITY.get(alias_type, Decimal("0.75"))),
                provenance=provenance,
                source_ref=source_ref,
                source_batch_id=source_batch.id if source_batch is not None else None,
                source_row_id=source_row.id if source_row is not None else None,
                source_metadata=alias.get("source_metadata") or {},
            )
            db.add(row)
            attached.append(row)
        db.flush()
        return attached

    def _missing_aliases(self, db: Session, vendor: Vendor, normalized: dict[str, str]) -> list[dict[str, Any]]:
        existing = {
            (row.alias_type, row.normalized_value)
            for row in db.query(VendorIdentityAlias)
            .filter(VendorIdentityAlias.vendor_id == vendor.id)
            .all()
        }
        return [
            alias for alias in self._aliases_from_normalized(normalized)
            if (alias["alias_type"], alias["normalized_value"]) not in existing
        ]

    def _aliases_from_normalized(self, normalized: dict[str, str]) -> list[dict[str, Any]]:
        aliases: list[dict[str, Any]] = []
        for alias_type, normalized_value in normalized.items():
            if alias_type not in self.SAFE_ALIAS_TYPES:
                continue
            aliases.append(
                {
                    "alias_type": alias_type,
                    "alias_value": normalized_value,
                    "normalized_value": normalized_value,
                    "confidence": _ALIAS_PRIORITY.get(alias_type, Decimal("0.75")),
                    "source_metadata": {},
                }
            )
        return aliases

    def _collision_key(self, normalized: dict[str, str]) -> str | None:
        return normalized.get("registration_number") or normalized.get("domain") or normalized.get("legal_name")

    def _normalize_name(self, value: Any) -> str | None:
        raw = re.sub(r"\s+", " ", str(value or "").strip().lower())
        if not raw:
            return None
        return re.sub(r"[^a-z0-9 ]+", "", raw).strip() or None

    def _normalize_domain(self, value: Any) -> str | None:
        raw = str(value or "").strip().lower()
        if not raw:
            return None
        if "@" in raw:
            raw = raw.split("@", 1)[1]
        raw = re.sub(r"^https?://", "", raw)
        raw = raw.split("/", 1)[0]
        raw = re.sub(r"^www\.", "", raw)
        return raw or None

    def _normalize_identifier(self, value: Any) -> str | None:
        raw = re.sub(r"[^a-zA-Z0-9]+", "", str(value or "").strip().upper())
        return raw or None

    def _normalize_external_source_id(self, value: Any, source_type: Any) -> str | None:
        cleaned = self._normalize_identifier(value)
        if not cleaned:
            return None
        source = re.sub(r"[^a-z0-9]+", "_", str(source_type or "unknown").strip().lower()).strip("_")
        return f"{source}:{cleaned}"


vendor_resolution_service = VendorResolutionService()
