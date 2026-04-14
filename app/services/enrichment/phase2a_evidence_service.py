"""
Phase 2A Batch 3 - Part 4: enrichment evidence assembly.

Behavior:
- assembles additive Phase 2A evidence bundles for a BOM line
- uses persisted Batch 2 offers/availability plus Batch 3 HS/tariff/freight lookups
- emits explicit uncertainty markers instead of inventing values
- stores the assembled Phase 2A bundle under bom_part.enrichment_json["phase2a"]
- preserves Phase 1 fallback behavior by leaving existing enrichment_json content intact
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.bom import BOM, BOMPart
from app.models.enrichment import PartToSkuMapping, SKUAvailabilitySnapshot, SKUOffer
from app.models.project import Project
from app.schemas.enrichment import LaneLookupContextDTO, Phase2AEvidenceBundleDTO
from app.services.enrichment.hs_mapping_service import hs_mapping_service
from app.services.enrichment.lane_rate_band_lookup_service import lane_rate_band_lookup_service
from app.services.enrichment.offer_ingestion_service import offer_ingestion_service
from app.services.enrichment.tariff_lookup_service import tariff_lookup_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_datetime(value: date | datetime | None) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


class Phase2AEvidenceService:
    def _latest_valid_offer(
        self,
        db: Session,
        *,
        mapping_id: str,
        as_of: datetime,
    ) -> SKUOffer | None:
        rows = (
            db.query(SKUOffer)
            .filter(SKUOffer.part_to_sku_mapping_id == mapping_id)
            .order_by(SKUOffer.observed_at.desc(), SKUOffer.updated_at.desc())
            .all()
        )
        for row in rows:
            valid_from_ok = row.valid_from is None or row.valid_from <= as_of
            valid_to_ok = row.valid_to is None or row.valid_to > as_of
            if valid_from_ok and valid_to_ok:
                return row
        return rows[0] if rows else None

    def _latest_snapshot(self, db: Session, *, sku_offer_id: str) -> SKUAvailabilitySnapshot | None:
        return (
            db.query(SKUAvailabilitySnapshot)
            .filter(SKUAvailabilitySnapshot.sku_offer_id == sku_offer_id)
            .order_by(SKUAvailabilitySnapshot.snapshot_at.desc(), SKUAvailabilitySnapshot.created_at.desc())
            .first()
        )

    def _select_mapping_offer_bundle(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        as_of: datetime,
    ) -> tuple[PartToSkuMapping | None, SKUOffer | None]:
        mappings = (
            db.query(PartToSkuMapping)
            .filter(PartToSkuMapping.bom_part_id == bom_part.id)
            .order_by(
                PartToSkuMapping.is_preferred.desc(),
                PartToSkuMapping.confidence.desc(),
                PartToSkuMapping.updated_at.desc(),
            )
            .all()
        )
        for mapping in mappings:
            offer = self._latest_valid_offer(db, mapping_id=mapping.id, as_of=as_of)
            if offer is not None:
                return mapping, offer
        return (mappings[0], None) if mappings else (None, None)

    def _collapse_status(self, statuses: list[str]) -> str:
        normalized = [str(s or "unknown").lower() for s in statuses if s]
        if not normalized:
            return "missing"
        if any(s in {"stale", "expired", "uncertain", "unknown", "missing"} for s in normalized):
            return "mixed"
        return "fresh"

    def _confidence_summary(self, values: list[Decimal | None]) -> dict[str, Any]:
        clean = [float(v) for v in values if v is not None]
        if not clean:
            return {"status": "unknown", "score": 0.0, "component_count": 0}

        score = round(sum(clean) / len(clean), 4)
        if score >= 0.8:
            status = "high"
        elif score >= 0.5:
            status = "medium"
        else:
            status = "low"

        return {
            "status": status,
            "score": score,
            "component_count": len(clean),
        }

    def assemble_for_bom_part(
        self,
        db: Session,
        *,
        bom_part: BOMPart,
        bom: BOM | None = None,
        project: Project | None = None,
        target_currency: str | None = None,
        lookup_date: date | datetime | None = None,
        trace_id: str | None = None,
    ) -> Phase2AEvidenceBundleDTO:
        as_of = _to_utc_datetime(lookup_date)
        bom = bom or db.query(BOM).filter(BOM.id == bom_part.bom_id).first()

        quantity = _as_decimal(bom_part.quantity, "1")
        selected_mapping, selected_offer = self._select_mapping_offer_bundle(
            db,
            bom_part=bom_part,
            as_of=as_of,
        )

        selected_price_break = None
        offer_status = "missing"
        offer_confidence = _as_decimal(selected_mapping.confidence) if selected_mapping else None
        origin_country = None
        origin_region = None

        offer_evidence: dict[str, Any] = {
            "selected_mapping_id": selected_mapping.id if selected_mapping else None,
            "selected_offer_id": selected_offer.id if selected_offer else None,
            "uncertain": selected_offer is None,
            "uncertainty_reason": None if selected_offer else "offer_missing",
        }

        if selected_offer is not None:
            selected_price_break = offer_ingestion_service.resolve_best_price_break(
                db,
                sku_offer_id=selected_offer.id,
                quantity=quantity,
            )
            offer_status = str(selected_offer.freshness_status or "FRESH").lower()
            origin_country = selected_offer.country_of_origin
            origin_region = selected_offer.factory_region

            offer_evidence = {
                "selected_mapping_id": selected_mapping.id if selected_mapping else None,
                "selected_offer_id": selected_offer.id,
                "offer_name": selected_offer.offer_name,
                "vendor_id": selected_offer.vendor_id,
                "vendor_sku": selected_mapping.vendor_sku if selected_mapping else None,
                "currency": selected_offer.currency,
                "target_currency": target_currency,
                "uom": selected_offer.uom,
                "moq": str(selected_offer.moq) if selected_offer.moq is not None else None,
                "spq": str(selected_offer.spq) if selected_offer.spq is not None else None,
                "lead_time_days": str(selected_offer.lead_time_days) if selected_offer.lead_time_days is not None else None,
                "valid_from": selected_offer.valid_from.isoformat() if selected_offer.valid_from else None,
                "valid_to": selected_offer.valid_to.isoformat() if selected_offer.valid_to else None,
                "freshness_status": selected_offer.freshness_status,
                "source_system": selected_offer.source_system,
                "source_metadata": selected_offer.source_metadata or {},
                "selected_price_break": {
                    "break_qty": str(selected_price_break.break_qty),
                    "unit_price": str(selected_price_break.unit_price),
                    "currency": selected_price_break.currency,
                    "price_type": selected_price_break.price_type,
                    "extended_price": (
                        str(selected_price_break.extended_price)
                        if selected_price_break.extended_price is not None
                        else None
                    ),
                } if selected_price_break else None,
                "uncertain": selected_price_break is None,
                "uncertainty_reason": None if selected_price_break else "price_break_missing",
            }

        latest_snapshot = self._latest_snapshot(db, sku_offer_id=selected_offer.id) if selected_offer else None
        availability_status = "missing"
        availability_confidence = None

        availability_evidence: dict[str, Any] = {
            "selected_offer_id": selected_offer.id if selected_offer else None,
            "uncertain": latest_snapshot is None,
            "uncertainty_reason": None if latest_snapshot else "availability_missing",
        }

        if latest_snapshot is not None:
            feasibility_tag = (latest_snapshot.source_metadata or {}).get("feasibility_tag")
            availability_status = str(latest_snapshot.freshness_status or "FRESH").lower()
            availability_confidence = Decimal("1") if feasibility_tag in {"feasible_now", "feasible_by_date"} else Decimal("0.5")

            availability_evidence = {
                "snapshot_id": latest_snapshot.id,
                "selected_offer_id": selected_offer.id if selected_offer else None,
                "availability_status": latest_snapshot.availability_status,
                "available_qty": str(latest_snapshot.available_qty) if latest_snapshot.available_qty is not None else None,
                "on_order_qty": str(latest_snapshot.on_order_qty) if latest_snapshot.on_order_qty is not None else None,
                "allocated_qty": str(latest_snapshot.allocated_qty) if latest_snapshot.allocated_qty is not None else None,
                "backorder_qty": str(latest_snapshot.backorder_qty) if latest_snapshot.backorder_qty is not None else None,
                "factory_lead_time_days": str(latest_snapshot.factory_lead_time_days) if latest_snapshot.factory_lead_time_days is not None else None,
                "inventory_location": latest_snapshot.inventory_location,
                "snapshot_at": latest_snapshot.snapshot_at.isoformat() if latest_snapshot.snapshot_at else None,
                "freshness_status": latest_snapshot.freshness_status,
                "feasibility_tag": feasibility_tag,
                "feasible": feasibility_tag in {"feasible_now", "feasible_by_date"},
                "source_system": latest_snapshot.source_system,
                "source_metadata": latest_snapshot.source_metadata or {},
                "uncertain": False,
                "uncertainty_reason": None,
            }

        hs_resolution = hs_mapping_service.resolve_for_bom_part(
            db,
            bom_part=bom_part,
            trace_id=trace_id,
        )

        customs_value = None
        if selected_price_break is not None:
            customs_value = selected_price_break.unit_price * quantity

        destination_country = None
        if project and isinstance(project.project_metadata, dict):
            destination_country = project.project_metadata.get("destination_country")
        if destination_country is None and bom and isinstance(bom.parse_summary, dict):
            destination_country = bom.parse_summary.get("destination_country")
        destination_country = destination_country or "USA"

        tariff_result = tariff_lookup_service.lookup_for_hs_resolution(
            db,
            hs_resolution=hs_resolution,
            destination_country=destination_country,
            origin_country=origin_country,
            lookup_date=as_of,
            customs_value=customs_value,
            bom_part=bom_part,
            trace_id=trace_id,
        )

        tariff_evidence = {
            "hs_code": hs_resolution.hs_code,
            "hs_resolution_status": hs_resolution.resolution_status,
            "hs_uncertainty_reason": hs_resolution.uncertainty_reason,
            "tariff_schedule_id": tariff_result.tariff_schedule_id,
            "destination_country": tariff_result.destination_country,
            "origin_country": tariff_result.origin_country,
            "duty_rate_pct": str(tariff_result.duty_rate_pct),
            "additional_taxes_pct": str(tariff_result.additional_taxes_pct),
            "total_tariff_rate_pct": str(tariff_result.total_tariff_rate_pct),
            "estimated_customs_value": (
                str(tariff_result.estimated_customs_value)
                if tariff_result.estimated_customs_value is not None
                else None
            ),
            "estimated_total_tariff": (
                str(tariff_result.estimated_total_tariff)
                if tariff_result.estimated_total_tariff is not None
                else None
            ),
            "freshness_status": tariff_result.freshness_status,
            "confidence": str(tariff_result.confidence),
            "resolved": tariff_result.resolved,
            "uncertain": not tariff_result.resolved,
            "uncertainty_reason": tariff_result.uncertainty_reason,
            "source_metadata": tariff_result.source_metadata,
        }

        weight_kg = None
        if isinstance(bom_part.specs, dict):
            for key in ("weight_kg", "estimated_weight_kg"):
                if bom_part.specs.get(key) not in (None, ""):
                    weight_kg = _as_decimal(bom_part.specs.get(key))
                    break

        lane_context = LaneLookupContextDTO(
            origin_country=origin_country,
            origin_region=origin_region,
            destination_country=destination_country,
            destination_region=bom.delivery_location if bom else None,
            mode=(
                project.project_metadata.get("shipping_mode")
                if project and isinstance(project.project_metadata, dict)
                else None
            ) or "sea",
            service_level=(
                project.project_metadata.get("service_level")
                if project and isinstance(project.project_metadata, dict)
                else None
            ),
            weight_kg=weight_kg,
        )

        lane_result = lane_rate_band_lookup_service.lookup_lane_rate(
            db,
            context=lane_context,
            project=project,
            bom=bom,
            bom_part=bom_part,
            lookup_date=as_of,
            trace_id=trace_id,
        )

        freight_evidence = {
            "lane_rate_band_id": lane_result.lane_rate_band_id,
            "origin_country": lane_result.origin_country,
            "origin_region": lane_result.origin_region,
            "destination_country": lane_result.destination_country,
            "destination_region": lane_result.destination_region,
            "mode": lane_result.mode,
            "service_level": lane_result.service_level,
            "currency": lane_result.currency,
            "p50_freight_estimate": str(lane_result.p50_freight_estimate) if lane_result.p50_freight_estimate is not None else None,
            "p90_freight_estimate": str(lane_result.p90_freight_estimate) if lane_result.p90_freight_estimate is not None else None,
            "transit_days_min": lane_result.transit_days_min,
            "transit_days_max": lane_result.transit_days_max,
            "freshness_status": lane_result.freshness_status,
            "confidence": str(lane_result.confidence),
            "resolved": lane_result.resolved,
            "uncertain": not lane_result.resolved,
            "uncertainty_reason": lane_result.uncertainty_reason,
            "source_metadata": lane_result.source_metadata,
        }

        tariff_status = str(
            tariff_result.freshness_status or ("uncertain" if not tariff_result.resolved else "fresh")
        ).lower()
        freight_status = str(
            lane_result.freshness_status or ("uncertain" if not lane_result.resolved else "fresh")
        ).lower()

        freshness_summary = {
            "status": self._collapse_status(
                [offer_status, availability_status, tariff_status, freight_status]
            ),
            "offer_status": offer_status,
            "availability_status": availability_status,
            "tariff_status": tariff_status,
            "freight_status": freight_status,
        }

        confidence_summary = self._confidence_summary(
            [
                offer_confidence,
                availability_confidence,
                hs_resolution.confidence,
                tariff_result.confidence,
                lane_result.confidence,
            ]
        )

        uncertainty_flags = {
            "offer_missing": selected_offer is None,
            "availability_missing": latest_snapshot is None,
            "tariff_uncertain": not tariff_result.resolved,
            "freight_uncertain": not lane_result.resolved,
            "hs_uncertain": not hs_resolution.resolved,
        }

        notes: list[str] = []
        if uncertainty_flags["offer_missing"]:
            notes.append("Phase 2A offer evidence missing; Phase 1 fallback pricing remains authoritative.")
        if uncertainty_flags["availability_missing"]:
            notes.append("Phase 2A availability evidence missing; feasibility remains uncertain.")
        if uncertainty_flags["tariff_uncertain"]:
            notes.append("Tariff evidence unresolved or low confidence; no tariff value was invented.")
        if uncertainty_flags["freight_uncertain"]:
            notes.append("Freight lane evidence unresolved; Phase 1 freight baseline remains fallback.")

        bundle = Phase2AEvidenceBundleDTO(
            bom_part_id=bom_part.id,
            offer_evidence=offer_evidence,
            availability_evidence=availability_evidence,
            tariff_evidence=tariff_evidence,
            freight_evidence=freight_evidence,
            freshness_summary=freshness_summary,
            confidence_summary=confidence_summary,
            uncertainty_flags=uncertainty_flags,
            notes=notes,
        )

        existing_enrichment = dict(bom_part.enrichment_json or {})
        existing_enrichment["phase2a"] = {
            "bom_part_id": bundle.bom_part_id,
            "offer_evidence": bundle.offer_evidence,
            "availability_evidence": bundle.availability_evidence,
            "tariff_evidence": bundle.tariff_evidence,
            "freight_evidence": bundle.freight_evidence,
            "freshness_summary": bundle.freshness_summary,
            "confidence_summary": bundle.confidence_summary,
            "uncertainty_flags": bundle.uncertainty_flags,
            "notes": bundle.notes,
        }
        bom_part.enrichment_json = existing_enrichment
        bom_part.enrichment_status = "COMPLETE"
        bom_part.data_freshness_json = {
            **(bom_part.data_freshness_json or {}),
            "phase2a": freshness_summary,
        }

        return bundle


phase2a_evidence_service = Phase2AEvidenceService()