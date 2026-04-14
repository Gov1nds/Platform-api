"""
Phase 2A Batch 2: offer ingestion pipeline.

Behavior:
- uses resolved SKU candidates
- calls connector abstraction for pricing/offers
- normalizes into sku_offers + sku_offer_price_breaks
- computes/stores data_hash and ttl metadata
- deduplicates idempotently
- supports quantity-break lookup for a BOM quantity
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.integrations.distributor_connector import (
    NullProductDataConnector,
    ProductDataConnector,
)
from app.models.bom import BOMPart
from app.models.enrichment import (
    EnrichmentRunLog,
    PartToSkuMapping,
    SKUOffer,
    SKUOfferPriceBreak,
)
from app.schemas.enrichment import OfferDTO, PriceBreakDTO, ProductSearchCandidate, ResolvedOfferPrice


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _ttl_valid_to(observed_at: datetime, ttl_seconds: int | None, explicit_valid_to: datetime | None) -> datetime | None:
    if explicit_valid_to is not None:
        return explicit_valid_to
    if ttl_seconds is None:
        return None
    return observed_at + timedelta(seconds=ttl_seconds)


def _mapping_candidate(mapping: PartToSkuMapping) -> ProductSearchCandidate:
    metadata = mapping.source_metadata or {}
    return ProductSearchCandidate(
        vendor_id=mapping.vendor_id,
        vendor_sku=mapping.vendor_sku,
        manufacturer=mapping.manufacturer,
        mpn=mapping.mpn,
        normalized_mpn=mapping.normalized_mpn,
        canonical_part_key=mapping.canonical_part_key,
        match_method=mapping.match_method,
        match_score=_as_decimal(mapping.confidence),
        mapping_status=str(metadata.get("mapping_status") or "resolved"),
        source_system=mapping.source_system,
        source_record_id=mapping.source_record_id,
        source_metadata=metadata,
        is_preferred=bool(mapping.is_preferred),
    )


class OfferIngestionService:
    STAGE = "offer_ingestion"

    def _get_or_create_run_log(
        self,
        db: Session,
        *,
        bom_part: BOMPart | None,
        provider: str,
        request_hash: str,
        mapping_id: str,
    ) -> EnrichmentRunLog:
        idempotency_key = f"{self.STAGE}:{mapping_id}:{request_hash}"
        existing = (
            db.query(EnrichmentRunLog)
            .filter(EnrichmentRunLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing

        log = EnrichmentRunLog(
            bom_id=bom_part.bom_id if bom_part else None,
            bom_part_id=bom_part.id if bom_part else None,
            run_scope="bom_line" if bom_part else "batch",
            stage=self.STAGE,
            provider=provider,
            status="started",
            idempotency_key=idempotency_key,
            attempt_count=1,
            request_hash=request_hash,
            source_system="platform-api",
            source_metadata={},
            started_at=_now(),
        )
        db.add(log)
        db.flush()
        return log

    def _upsert_offer(
        self,
        db: Session,
        *,
        mapping: PartToSkuMapping,
        offer: OfferDTO,
    ) -> SKUOffer:
        observed_at = offer.observed_at or _now()
        valid_from = offer.valid_from or observed_at
        valid_to = _ttl_valid_to(observed_at, offer.ttl_seconds, offer.valid_to)

        normalized_payload = {
            "mapping_id": mapping.id,
            "vendor_id": offer.vendor_id or mapping.vendor_id,
            "vendor_sku": mapping.vendor_sku,
            "offer_name": offer.offer_name,
            "currency": offer.currency,
            "uom": offer.uom,
            "moq": str(offer.moq) if offer.moq is not None else None,
            "spq": str(offer.spq) if offer.spq is not None else None,
            "lead_time_days": str(offer.lead_time_days) if offer.lead_time_days is not None else None,
            "country_of_origin": offer.country_of_origin,
            "price_breaks": [
                {
                    "break_qty": str(pb.break_qty),
                    "unit_price": str(pb.unit_price),
                    "currency": pb.currency,
                    "price_type": pb.price_type,
                }
                for pb in offer.price_breaks
            ],
        }
        data_hash = _hash_payload(normalized_payload)

        row = (
            db.query(SKUOffer)
            .filter(
                (SKUOffer.source_record_hash == data_hash) |
                (
                    (SKUOffer.source_system == offer.source_system) &
                    (SKUOffer.source_record_id == offer.source_record_id)
                )
            )
            .first()
        )

        source_metadata = dict(offer.source_metadata or {})
        source_metadata.update(
            {
                "data_hash": data_hash,
                "ttl_seconds": offer.ttl_seconds,
                "vendor_sku": mapping.vendor_sku,
            }
        )

        if row is None:
            row = SKUOffer(
                part_to_sku_mapping_id=mapping.id,
                vendor_id=offer.vendor_id or mapping.vendor_id,
                offer_name=offer.offer_name,
                offer_status=offer.offer_status,
                currency=offer.currency,
                uom=offer.uom,
                moq=offer.moq,
                spq=offer.spq,
                lead_time_days=offer.lead_time_days,
                packaging=offer.packaging,
                incoterm=offer.incoterm,
                country_of_origin=offer.country_of_origin,
                factory_region=offer.factory_region,
                is_authorized=offer.is_authorized,
                freshness_status="FRESH",
                observed_at=observed_at,
                valid_from=valid_from,
                valid_to=valid_to,
                source_system=offer.source_system,
                source_record_id=offer.source_record_id,
                source_record_hash=data_hash,
                source_metadata=source_metadata,
            )
            db.add(row)
            db.flush()
        else:
            row.part_to_sku_mapping_id = mapping.id
            row.vendor_id = offer.vendor_id or mapping.vendor_id
            row.offer_name = offer.offer_name
            row.offer_status = offer.offer_status
            row.currency = offer.currency
            row.uom = offer.uom
            row.moq = offer.moq
            row.spq = offer.spq
            row.lead_time_days = offer.lead_time_days
            row.packaging = offer.packaging
            row.incoterm = offer.incoterm
            row.country_of_origin = offer.country_of_origin
            row.factory_region = offer.factory_region
            row.is_authorized = offer.is_authorized
            row.freshness_status = "FRESH"
            row.observed_at = observed_at
            row.valid_from = valid_from
            row.valid_to = valid_to
            row.source_system = offer.source_system
            row.source_record_id = offer.source_record_id
            row.source_record_hash = data_hash
            row.source_metadata = source_metadata
            row.updated_at = _now()
            db.flush()

        # Replace current price breaks for this offer version.
        db.query(SKUOfferPriceBreak).filter(
            SKUOfferPriceBreak.sku_offer_id == row.id
        ).delete(synchronize_session=False)

        for price_break in offer.price_breaks:
            self._insert_price_break(db, sku_offer=row, price_break=price_break, data_hash=data_hash)

        db.flush()
        return row

    def _insert_price_break(
        self,
        db: Session,
        *,
        sku_offer: SKUOffer,
        price_break: PriceBreakDTO,
        data_hash: str,
    ) -> None:
        break_hash = _hash_payload(
            {
                "offer_hash": data_hash,
                "break_qty": str(price_break.break_qty),
                "unit_price": str(price_break.unit_price),
                "currency": price_break.currency,
                "price_type": price_break.price_type,
            }
        )
        row = SKUOfferPriceBreak(
            sku_offer_id=sku_offer.id,
            break_qty=price_break.break_qty,
            unit_price=price_break.unit_price,
            currency=price_break.currency,
            extended_price=price_break.extended_price,
            price_type=price_break.price_type,
            valid_from=sku_offer.valid_from,
            valid_to=sku_offer.valid_to,
            source_record_hash=break_hash,
            source_metadata=price_break.source_metadata or {},
        )
        db.add(row)

    def ingest_for_mapping(
        self,
        db: Session,
        *,
        mapping: PartToSkuMapping,
        connector: ProductDataConnector | None = None,
        bom_part: BOMPart | None = None,
    ) -> list[SKUOffer]:
        connector = connector or NullProductDataConnector()
        candidate = _mapping_candidate(mapping)

        request_hash = _hash_payload(
            {
                "mapping_id": mapping.id,
                "vendor_id": mapping.vendor_id,
                "vendor_sku": mapping.vendor_sku,
                "provider": connector.provider_name,
            }
        )
        run_log = self._get_or_create_run_log(
            db,
            bom_part=bom_part,
            provider=connector.provider_name,
            request_hash=request_hash,
            mapping_id=mapping.id,
        )

        try:
            offers = connector.fetch_offers(candidate)
            rows = [self._upsert_offer(db, mapping=mapping, offer=offer) for offer in offers]
            run_log.records_written = len(rows)
            run_log.records_skipped = 0
            run_log.status = "success"
            run_log.source_metadata = {"offer_count": len(offers)}
            run_log.completed_at = _now()
            run_log.duration_ms = int((run_log.completed_at - run_log.started_at).total_seconds() * 1000)
            return rows
        except Exception as exc:
            run_log.status = "failed"
            run_log.error_message = str(exc)[:500]
            run_log.completed_at = _now()
            run_log.duration_ms = int((run_log.completed_at - run_log.started_at).total_seconds() * 1000)
            raise

    def resolve_best_price_break(
        self,
        db: Session,
        *,
        sku_offer_id: str,
        quantity: Decimal,
    ) -> ResolvedOfferPrice | None:
        rows = (
            db.query(SKUOfferPriceBreak)
            .filter(SKUOfferPriceBreak.sku_offer_id == sku_offer_id)
            .order_by(desc(SKUOfferPriceBreak.break_qty))
            .all()
        )
        if not rows:
            return None

        chosen = None
        for row in rows:
            if _as_decimal(row.break_qty) <= quantity:
                chosen = row
                break
        if chosen is None:
            chosen = rows[-1]

        return ResolvedOfferPrice(
            break_qty=_as_decimal(chosen.break_qty),
            unit_price=_as_decimal(chosen.unit_price),
            currency=chosen.currency,
            price_type=chosen.price_type,
            extended_price=_as_decimal(chosen.extended_price) if chosen.extended_price is not None else None,
        )


offer_ingestion_service = OfferIngestionService()