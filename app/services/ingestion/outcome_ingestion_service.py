"""
Outcome Ingestion service (Phase 3).

Implements Execution Plan §8 "Quote/PO Histories" + "Performance Records
continuous update" + §9 evidence capture.

Wraps the existing app.services.outcome_data_service (Phase 2C) and adds
Phase-3 side-effects:
 - update pricing.part_vendor_index (PartVendorMatcher.update_index_from_outcome)
 - update pricing.vendor_communication_scores on RFQ response
 - insert pricing.vendor_lead_time_history (Phase 3) rows for P90 distribution
 - recompute trust tier on PO outcome

This service is additive. The legacy outcome_data_service continues to
own QuoteOutcome / VendorPerformance / LeadTimeHistory (Phase 2C).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.vendor import VendorCommunicationScore
from app.services.market.lead_time_intelligence_service import (
    lead_time_intelligence_phase3_service,
)
from app.services.market.market_anomaly_service import market_anomaly_service
from app.services.matching.part_vendor_matcher import part_vendor_matcher
from app.services.vendor_intelligence_service import vendor_intelligence_service

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


class OutcomeIngestionPhase3Service:
    """Phase-3 outcome ingestion helpers."""

    # ── RFQ outcome ──────────────────────────────────────────────────────

    def record_rfq_outcome(
        self,
        rfq_id: str,
        vendor_id: str,
        canonical_part_key: str,
        quoted_price: Any,
        currency: str | None,
        quoted_lead_time_days: Any,
        response_time_hours: Any,
        db: Session,
        responded: bool = True,
        market_median_price: Any = None,
    ) -> dict[str, Any]:
        # Update PartVendorIndex with the RFQ response
        part_vendor_matcher.update_index_from_outcome(
            canonical_part_key=canonical_part_key,
            vendor_id=vendor_id,
            outcome_type="rfq_response" if responded else "rfq_sent",
            outcome_data={
                "quoted_price": str(_as_decimal(quoted_price)) if quoted_price is not None else None,
                "currency": currency,
                "quote_date": _now().date().isoformat(),
                "quoted_lead_time_days": str(_as_decimal(quoted_lead_time_days))
                if quoted_lead_time_days is not None
                else None,
                "source_rfq_id": rfq_id,
            },
            db=db,
        )

        # Update communication score
        self._update_communication_score(
            db=db,
            vendor_id=vendor_id,
            responded=responded,
            response_time_hours=_as_decimal(response_time_hours),
        )

        # Anomaly scan on quoted price + lead time
        flags = market_anomaly_service.check_quote_for_anomalies(
            vendor_id=vendor_id,
            canonical_part_key=canonical_part_key,
            quoted_price=_as_decimal(quoted_price),
            quoted_lead_time_days=_as_decimal(quoted_lead_time_days),
            db=db,
            market_median_price=_as_decimal(market_median_price),
        )

        logger.info(
            "record_rfq_outcome vendor=%s part=%s responded=%s anomalies=%d",
            vendor_id, canonical_part_key, responded, len(flags),
        )
        return {
            "vendor_id": vendor_id,
            "canonical_part_key": canonical_part_key,
            "responded": responded,
            "anomaly_count": len(flags),
        }

    # ── PO outcome ───────────────────────────────────────────────────────

    def record_po_outcome(
        self,
        po_id: str,
        vendor_id: str,
        canonical_part_key: str,
        actual_price: Any,
        actual_lead_time_days: Any,
        quality_passed: bool,
        db: Session,
        quoted_lead_time_days: Any = None,
        category_tag: str | None = None,
        material_family: str | None = None,
    ) -> dict[str, Any]:
        # 1. Part-vendor index
        part_vendor_matcher.update_index_from_outcome(
            canonical_part_key=canonical_part_key,
            vendor_id=vendor_id,
            outcome_type="po_delivered" if quality_passed else "po_failed",
            outcome_data={
                "actual_price": str(_as_decimal(actual_price)) if actual_price is not None else None,
                "actual_lead_time_days": str(_as_decimal(actual_lead_time_days))
                if actual_lead_time_days is not None
                else None,
                "quality_passed": bool(quality_passed),
                "po_id": po_id,
                "po_date": _now().date().isoformat(),
            },
            db=db,
        )

        # 2. Lead-time history (Phase 3)
        if actual_lead_time_days is not None:
            lead_time_intelligence_phase3_service.record_actual_lead_time(
                vendor_id=vendor_id,
                category_tag=category_tag,
                actual_days=_as_decimal(actual_lead_time_days) or Decimal("0"),
                quoted_days=_as_decimal(quoted_lead_time_days),
                source_po_id=po_id,
                db=db,
                material_family=material_family,
            )

        # 3. Recompute trust tier (performance snapshot should be
        # rebuilt by nightly worker — here we refresh denormalized columns)
        try:
            vendor_intelligence_service.compute_trust_tier(vendor_id, db)
        except Exception:
            logger.exception("trust_tier refresh failed on PO outcome vendor=%s", vendor_id)

        logger.info(
            "record_po_outcome po=%s vendor=%s part=%s quality_passed=%s",
            po_id, vendor_id, canonical_part_key, quality_passed,
        )
        return {
            "po_id": po_id,
            "vendor_id": vendor_id,
            "canonical_part_key": canonical_part_key,
            "quality_passed": bool(quality_passed),
        }

    # ── Communication score ──────────────────────────────────────────────

    def _update_communication_score(
        self,
        db: Session,
        vendor_id: str,
        responded: bool,
        response_time_hours: Decimal | None,
    ) -> VendorCommunicationScore:
        record = (
            db.query(VendorCommunicationScore)
            .filter(VendorCommunicationScore.vendor_id == vendor_id)
            .first()
        )
        if record is None:
            record = VendorCommunicationScore(vendor_id=vendor_id)
            db.add(record)
            db.flush()

        record.total_rfqs_sent = int(record.total_rfqs_sent or 0) + 1
        if responded:
            record.total_rfqs_responded = int(record.total_rfqs_responded or 0) + 1
            if response_time_hours is not None and response_time_hours > 0:
                prior = record.avg_response_time_hours
                if prior is None or int(record.total_rfqs_responded) <= 1:
                    record.avg_response_time_hours = response_time_hours
                else:
                    n = Decimal(str(record.total_rfqs_responded))
                    record.avg_response_time_hours = (
                        (Decimal(str(prior)) * (n - Decimal("1")) + response_time_hours) / n
                    ).quantize(Decimal("0.0001"))

        sent = int(record.total_rfqs_sent or 0)
        responded_n = int(record.total_rfqs_responded or 0)
        if sent > 0:
            rate = Decimal(str(responded_n)) / Decimal(str(sent))
            record.rfq_response_rate = rate.quantize(Decimal("0.0001"))

        # Quality score: responsiveness × speed
        quality = Decimal("0.5")
        if record.rfq_response_rate is not None:
            quality = Decimal(str(record.rfq_response_rate))
        if record.avg_response_time_hours:
            # Scale: ≤ 24h → full credit, 24–168h linear, > 168h → 0.3
            hrs = Decimal(str(record.avg_response_time_hours))
            if hrs <= Decimal("24"):
                speed = Decimal("1.0")
            elif hrs <= Decimal("168"):
                speed = Decimal("1.0") - ((hrs - Decimal("24")) / Decimal("144")) * Decimal("0.7")
            else:
                speed = Decimal("0.3")
            quality = ((quality * Decimal("0.6")) + (speed * Decimal("0.4"))).quantize(Decimal("0.0001"))
        record.communication_quality_score = quality
        record.last_computed_at = _now()

        # Reflect onto denormalized vendor column
        from app.models.vendor import Vendor as _Vendor
        vendor_row = db.query(_Vendor).filter(_Vendor.id == vendor_id).first()
        if vendor_row is not None:
            vendor_row.communication_score = quality
        return record


outcome_ingestion_phase3_service = OutcomeIngestionPhase3Service()
