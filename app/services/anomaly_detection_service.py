from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.canonical import CanonicalAvailabilitySnapshot, CanonicalOfferSnapshot, SourceSKULink
from app.models.enrichment import PartToSkuMapping, SKUAvailabilitySnapshot, SKUOffer, SKUOfferPriceBreak
from app.models.outcomes import AnomalyFlag, LeadTimeHistory, QuoteOutcome, VendorPerformance


DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_THREE = Decimal("3")
DECIMAL_POINT_THREE = Decimal("0.3")
DECIMAL_FIVE = Decimal("5")
DECIMAL_POINT_TWO = Decimal("0.2")
DECIMAL_TWO = Decimal("2")
DECIMAL_FOURTEEN = Decimal("14")
DECIMAL_ONE_THOUSAND = Decimal("1000")
DECIMAL_ONE_MILLION = Decimal("1000000")
DECIMAL_QUANTIZE = Decimal("0.00000001")

OUT_OF_STOCK_STATUSES = {"OUT_OF_STOCK", "BACKORDER", "UNAVAILABLE", "NO_STOCK"}
IN_STOCK_STATUSES = {"IN_STOCK", "AVAILABLE", "LIMITED", "LOW_STOCK"}


@dataclass
class _Baseline:
    values: list[Decimal]
    reference_value: Decimal | None

    @property
    def sample_size(self) -> int:
        return len(self.values)


class AnomalyDetectionService:
    """Deterministic anomaly detection for Phase 2C Batch 2C.3."""

    sparse_price_multiplier_high = Decimal("5")
    sparse_price_multiplier_low = Decimal("0.2")
    sparse_lead_multiplier_high = Decimal("2")
    sparse_lead_multiplier_low = Decimal("0.3")
    sparse_lead_abs_diff_threshold = Decimal("14")
    contradiction_window = timedelta(hours=24)

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _to_decimal(self, value) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _quantize(self, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(DECIMAL_QUANTIZE)

    def _clean_positive_values(self, values: Iterable) -> list[Decimal]:
        cleaned: list[Decimal] = []
        for value in values:
            dec = self._to_decimal(value)
            if dec is None or dec <= DECIMAL_ZERO:
                continue
            cleaned.append(dec)
        return cleaned

    def _clean_values(self, values: Iterable) -> list[Decimal]:
        cleaned: list[Decimal] = []
        for value in values:
            dec = self._to_decimal(value)
            if dec is None:
                continue
            cleaned.append(dec)
        return cleaned

    def _median(self, values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / DECIMAL_TWO

    def _mean(self, values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values) / Decimal(len(values))

    def _population_stddev(self, values: list[Decimal]) -> Decimal | None:
        if len(values) < 2:
            return None
        mean = self._mean(values)
        if mean is None:
            return None
        variance = sum((value - mean) * (value - mean) for value in values) / Decimal(len(values))
        return variance.sqrt()

    def _escalate_severity(self, severity: str) -> str:
        if severity == "low":
            return "medium"
        if severity == "medium":
            return "high"
        return "high"

    def _repeat_occurrence_count(
        self,
        db: Session,
        *,
        entity_type: str,
        entity_id: str,
        metric_name: str,
        anomaly_type: str,
    ) -> int:
        return (
            db.query(AnomalyFlag)
            .filter(
                AnomalyFlag.entity_type == entity_type,
                AnomalyFlag.entity_id == entity_id,
                AnomalyFlag.metric_name == metric_name,
                AnomalyFlag.anomaly_type == anomaly_type,
            )
            .count()
        )

    def _price_severity(self, *, ratio: Decimal, sample_size: int, repeats: int) -> str:
        if ratio >= DECIMAL_FIVE or ratio <= DECIMAL_POINT_TWO:
            severity = "high"
        elif sample_size >= 5:
            severity = "medium"
        else:
            severity = "low"
        if sample_size >= 5:
            severity = self._escalate_severity(severity) if severity == "low" else severity
        if repeats >= 1:
            severity = self._escalate_severity(severity)
        return severity

    def _lead_time_severity(self, *, deviation_ratio: Decimal | None, sample_size: int, repeats: int, sparse_trigger: bool = False) -> str:
        if sparse_trigger:
            severity = "medium" if sample_size >= 1 else "low"
        elif deviation_ratio is not None and deviation_ratio >= DECIMAL_THREE:
            severity = "high"
        elif deviation_ratio is not None and deviation_ratio >= DECIMAL_TWO:
            severity = "medium"
        else:
            severity = "low"
        if sample_size >= 5 and severity == "low":
            severity = "medium"
        if repeats >= 1:
            severity = self._escalate_severity(severity)
        return severity

    def _availability_severity(self, *, qty_ratio: Decimal | None, repeats: int, invalid: bool = False) -> str:
        if invalid:
            severity = "high"
        elif qty_ratio is not None and qty_ratio >= DECIMAL_FIVE:
            severity = "high"
        elif qty_ratio is not None and qty_ratio >= DECIMAL_THREE:
            severity = "medium"
        else:
            severity = "low"
        if repeats >= 1:
            severity = self._escalate_severity(severity)
        return severity

    def _window_key(
        self,
        *,
        entity_type: str,
        entity_id: str,
        metric_name: str,
        anomaly_type: str,
        detected_at: datetime,
    ) -> str:
        bucket = detected_at.astimezone(timezone.utc).strftime("%Y%m%d")
        return f"{entity_type}:{entity_id}:{metric_name}:{anomaly_type}:{bucket}"

    def _persist_flag(
        self,
        db: Session,
        *,
        entity_type: str,
        entity_id: str,
        metric_name: str,
        observed_value: Decimal | None,
        threshold_value: Decimal | None,
        anomaly_type: str,
        severity: str,
        detected_at: datetime,
        source_context_json: dict | None = None,
    ) -> AnomalyFlag:
        dedupe_window_key = self._window_key(
            entity_type=entity_type,
            entity_id=entity_id,
            metric_name=metric_name,
            anomaly_type=anomaly_type,
            detected_at=detected_at,
        )
        existing = (
            db.query(AnomalyFlag)
            .filter(AnomalyFlag.dedupe_window_key == dedupe_window_key)
            .first()
        )
        if existing:
            return existing

        row = AnomalyFlag(
            anomaly_id=f"anomaly-{uuid.uuid4().hex}",
            entity_type=entity_type,
            entity_id=entity_id,
            metric_name=metric_name,
            observed_value=self._quantize(observed_value),
            threshold_value=self._quantize(threshold_value),
            anomaly_type=anomaly_type,
            severity=severity,
            detected_at=detected_at,
            source_context_json=source_context_json or {},
            dedupe_window_key=dedupe_window_key,
        )
        db.add(row)
        db.flush()
        return row

    def _price_baseline_for_quote_outcome(self, db: Session, row: QuoteOutcome) -> _Baseline:
        values: list[Decimal] = []

        prior_quotes = (
            db.query(QuoteOutcome.quoted_price)
            .filter(
                QuoteOutcome.bom_line_id == row.bom_line_id,
                QuoteOutcome.vendor_id == row.vendor_id,
                QuoteOutcome.id != row.id,
                QuoteOutcome.quoted_price.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in prior_quotes))

        canonical_quotes = (
            db.query(CanonicalOfferSnapshot.unit_price)
            .join(SourceSKULink, SourceSKULink.id == CanonicalOfferSnapshot.source_sku_link_id)
            .join(PartToSkuMapping, PartToSkuMapping.id == SourceSKULink.part_to_sku_mapping_id)
            .filter(
                PartToSkuMapping.bom_part_id == row.bom_line_id,
                CanonicalOfferSnapshot.vendor_id == row.vendor_id,
                CanonicalOfferSnapshot.unit_price.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in canonical_quotes))

        price_breaks = (
            db.query(SKUOfferPriceBreak.unit_price)
            .join(SKUOffer, SKUOffer.id == SKUOfferPriceBreak.sku_offer_id)
            .join(PartToSkuMapping, PartToSkuMapping.id == SKUOffer.part_to_sku_mapping_id)
            .filter(
                PartToSkuMapping.bom_part_id == row.bom_line_id,
                SKUOffer.vendor_id == row.vendor_id,
                SKUOfferPriceBreak.unit_price.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in price_breaks))

        return _Baseline(values=values, reference_value=self._median(values))

    def _price_baseline_for_canonical_offer(self, db: Session, row: CanonicalOfferSnapshot) -> _Baseline:
        values: list[Decimal] = []

        siblings = (
            db.query(CanonicalOfferSnapshot.unit_price)
            .filter(
                CanonicalOfferSnapshot.canonical_sku_id == row.canonical_sku_id,
                CanonicalOfferSnapshot.id != row.id,
                CanonicalOfferSnapshot.unit_price.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in siblings))

        if row.source_sku_link_id:
            mapped_quotes = (
                db.query(QuoteOutcome.quoted_price)
                .join(SourceSKULink, SourceSKULink.part_to_sku_mapping_id == QuoteOutcome.bom_line_id, isouter=False)
                .filter(False)
                .all()
            )
            # No repo-safe direct join from canonical snapshot to quote outcome beyond source link.
            # Keep baseline deterministic with canonical siblings when available.
            _ = mapped_quotes

        return _Baseline(values=values, reference_value=self._median(values))

    def _price_baseline_for_price_break(self, db: Session, row: SKUOfferPriceBreak) -> _Baseline:
        values: list[Decimal] = []

        same_offer = (
            db.query(SKUOfferPriceBreak.unit_price)
            .filter(
                SKUOfferPriceBreak.sku_offer_id == row.sku_offer_id,
                SKUOfferPriceBreak.id != row.id,
                SKUOfferPriceBreak.unit_price.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in same_offer))

        offer = db.query(SKUOffer).filter(SKUOffer.id == row.sku_offer_id).first()
        if offer and offer.part_to_sku_mapping_id and offer.vendor_id:
            other_breaks = (
                db.query(SKUOfferPriceBreak.unit_price)
                .join(SKUOffer, SKUOffer.id == SKUOfferPriceBreak.sku_offer_id)
                .filter(
                    SKUOffer.part_to_sku_mapping_id == offer.part_to_sku_mapping_id,
                    SKUOffer.vendor_id == offer.vendor_id,
                    SKUOfferPriceBreak.id != row.id,
                    SKUOfferPriceBreak.unit_price.is_not(None),
                )
                .all()
            )
            values.extend(self._clean_positive_values(value for (value,) in other_breaks))

        return _Baseline(values=values, reference_value=self._median(values))

    def _lead_time_baseline_for_quote_outcome(self, db: Session, row: QuoteOutcome) -> _Baseline:
        values: list[Decimal] = []

        prior_quotes = (
            db.query(QuoteOutcome.quoted_lead_time)
            .filter(
                QuoteOutcome.vendor_id == row.vendor_id,
                QuoteOutcome.bom_line_id == row.bom_line_id,
                QuoteOutcome.id != row.id,
                QuoteOutcome.quoted_lead_time.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in prior_quotes))

        actuals = (
            db.query(LeadTimeHistory.actual_lead_time)
            .filter(
                LeadTimeHistory.vendor_id == row.vendor_id,
                LeadTimeHistory.bom_line_id == row.bom_line_id,
                LeadTimeHistory.actual_lead_time.is_not(None),
            )
            .all()
        )
        values.extend(self._clean_positive_values(value for (value,) in actuals))

        perf = (
            db.query(VendorPerformance)
            .filter(VendorPerformance.vendor_id == row.vendor_id, VendorPerformance.avg_lead_time.is_not(None))
            .order_by(VendorPerformance.period_end.desc(), VendorPerformance.created_at.desc())
            .first()
        )
        reference = self._median(values)
        if reference is None and perf and perf.avg_lead_time is not None:
            reference = self._to_decimal(perf.avg_lead_time)
        return _Baseline(values=values, reference_value=reference)

    def _lead_time_diff_baseline(self, db: Session, row: LeadTimeHistory) -> _Baseline:
        diffs = (
            db.query(LeadTimeHistory.lead_time_diff_days)
            .filter(
                LeadTimeHistory.vendor_id == row.vendor_id,
                LeadTimeHistory.id != row.id,
                LeadTimeHistory.lead_time_diff_days.is_not(None),
            )
            .all()
        )
        values = self._clean_values(value for (value,) in diffs)
        return _Baseline(values=values, reference_value=self._mean(values))

    def detect_price_anomalies(
        self,
        db: Session,
        *,
        quote_outcome_ids: Iterable[str] | None = None,
        canonical_offer_snapshot_ids: Iterable[str] | None = None,
        sku_offer_price_break_ids: Iterable[str] | None = None,
        bom_line_id: str | None = None,
        vendor_id: str | None = None,
    ) -> list[AnomalyFlag]:
        detected: list[AnomalyFlag] = []

        quote_query = db.query(QuoteOutcome).filter(QuoteOutcome.quoted_price.is_not(None))
        if quote_outcome_ids:
            quote_query = quote_query.filter(QuoteOutcome.id.in_(list(quote_outcome_ids)))
        if bom_line_id:
            quote_query = quote_query.filter(QuoteOutcome.bom_line_id == bom_line_id)
        if vendor_id:
            quote_query = quote_query.filter(QuoteOutcome.vendor_id == vendor_id)

        for row in quote_query.all():
            observed = self._to_decimal(row.quoted_price)
            if observed is None or observed <= DECIMAL_ZERO:
                continue
            baseline = self._price_baseline_for_quote_outcome(db, row)
            reference = baseline.reference_value
            if reference is None or reference <= DECIMAL_ZERO:
                continue
            ratio = observed / reference
            if ratio > DECIMAL_THREE or ratio < DECIMAL_POINT_THREE:
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="quote_outcome",
                    entity_id=row.id,
                    metric_name="quoted_price",
                    anomaly_type="price_outlier",
                )
                severity = self._price_severity(ratio=ratio, sample_size=baseline.sample_size, repeats=repeats)
                detected.append(self._persist_flag(
                    db,
                    entity_type="quote_outcome",
                    entity_id=row.id,
                    metric_name="quoted_price",
                    observed_value=observed,
                    threshold_value=reference * (DECIMAL_THREE if ratio > DECIMAL_ONE else DECIMAL_POINT_THREE),
                    anomaly_type="price_outlier",
                    severity=severity,
                    detected_at=datetime.combine(row.quote_date, datetime.min.time(), tzinfo=timezone.utc) if row.quote_date else row.created_at,
                    source_context_json={
                        "baseline_median": str(reference),
                        "baseline_sample_size": baseline.sample_size,
                        "vendor_id": row.vendor_id,
                        "bom_line_id": row.bom_line_id,
                        "source_tables": ["pricing.quote_outcomes", "pricing.canonical_offer_snapshot", "pricing.sku_offer_price_breaks"],
                    },
                ))

        canonical_query = db.query(CanonicalOfferSnapshot).filter(CanonicalOfferSnapshot.unit_price.is_not(None))
        if canonical_offer_snapshot_ids:
            canonical_query = canonical_query.filter(CanonicalOfferSnapshot.id.in_(list(canonical_offer_snapshot_ids)))
        if vendor_id:
            canonical_query = canonical_query.filter(CanonicalOfferSnapshot.vendor_id == vendor_id)
        for row in canonical_query.all():
            observed = self._to_decimal(row.unit_price)
            if observed is None or observed <= DECIMAL_ZERO:
                continue
            baseline = self._price_baseline_for_canonical_offer(db, row)
            reference = baseline.reference_value
            if reference is None or reference <= DECIMAL_ZERO:
                continue
            ratio = observed / reference
            if ratio > DECIMAL_THREE or ratio < DECIMAL_POINT_THREE:
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="canonical_offer_snapshot",
                    entity_id=row.id,
                    metric_name="unit_price",
                    anomaly_type="price_outlier",
                )
                severity = self._price_severity(ratio=ratio, sample_size=baseline.sample_size, repeats=repeats)
                detected.append(self._persist_flag(
                    db,
                    entity_type="canonical_offer_snapshot",
                    entity_id=row.id,
                    metric_name="unit_price",
                    observed_value=observed,
                    threshold_value=reference * (DECIMAL_THREE if ratio > DECIMAL_ONE else DECIMAL_POINT_THREE),
                    anomaly_type="price_outlier",
                    severity=severity,
                    detected_at=row.observed_at,
                    source_context_json={
                        "baseline_median": str(reference),
                        "baseline_sample_size": baseline.sample_size,
                        "canonical_sku_id": row.canonical_sku_id,
                        "vendor_id": row.vendor_id,
                        "source_tables": ["pricing.canonical_offer_snapshot"],
                    },
                ))

        price_break_query = db.query(SKUOfferPriceBreak).filter(SKUOfferPriceBreak.unit_price.is_not(None))
        if sku_offer_price_break_ids:
            price_break_query = price_break_query.filter(SKUOfferPriceBreak.id.in_(list(sku_offer_price_break_ids)))
        for row in price_break_query.all():
            observed = self._to_decimal(row.unit_price)
            if observed is None or observed <= DECIMAL_ZERO:
                continue
            baseline = self._price_baseline_for_price_break(db, row)
            reference = baseline.reference_value
            if reference is None or reference <= DECIMAL_ZERO:
                continue
            ratio = observed / reference
            if ratio > DECIMAL_THREE or ratio < DECIMAL_POINT_THREE:
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="sku_offer_price_break",
                    entity_id=row.id,
                    metric_name="unit_price",
                    anomaly_type="price_outlier",
                )
                severity = self._price_severity(ratio=ratio, sample_size=baseline.sample_size, repeats=repeats)
                detected.append(self._persist_flag(
                    db,
                    entity_type="sku_offer_price_break",
                    entity_id=row.id,
                    metric_name="unit_price",
                    observed_value=observed,
                    threshold_value=reference * (DECIMAL_THREE if ratio > DECIMAL_ONE else DECIMAL_POINT_THREE),
                    anomaly_type="price_outlier",
                    severity=severity,
                    detected_at=row.valid_from,
                    source_context_json={
                        "baseline_median": str(reference),
                        "baseline_sample_size": baseline.sample_size,
                        "sku_offer_id": row.sku_offer_id,
                        "source_tables": ["pricing.sku_offer_price_breaks"],
                    },
                ))

        return detected

    def detect_lead_time_anomalies(
        self,
        db: Session,
        *,
        quote_outcome_ids: Iterable[str] | None = None,
        lead_time_history_ids: Iterable[str] | None = None,
        vendor_id: str | None = None,
        bom_line_id: str | None = None,
    ) -> list[AnomalyFlag]:
        detected: list[AnomalyFlag] = []

        quote_query = db.query(QuoteOutcome).filter(QuoteOutcome.quoted_lead_time.is_not(None))
        if quote_outcome_ids:
            quote_query = quote_query.filter(QuoteOutcome.id.in_(list(quote_outcome_ids)))
        if vendor_id:
            quote_query = quote_query.filter(QuoteOutcome.vendor_id == vendor_id)
        if bom_line_id:
            quote_query = quote_query.filter(QuoteOutcome.bom_line_id == bom_line_id)

        for row in quote_query.all():
            observed = self._to_decimal(row.quoted_lead_time)
            if observed is None or observed < DECIMAL_ZERO:
                continue
            baseline = self._lead_time_baseline_for_quote_outcome(db, row)
            reference = baseline.reference_value
            if reference is None or reference <= DECIMAL_ZERO:
                continue

            values = baseline.values
            mean = self._mean(values)
            stddev = self._population_stddev(values)
            threshold = None
            sparse_trigger = False
            deviation_ratio = None

            if baseline.sample_size >= 3 and mean is not None and stddev is not None:
                upper = mean + (DECIMAL_TWO * stddev)
                lower = max(DECIMAL_ZERO, mean - (DECIMAL_TWO * stddev))
                threshold = upper if observed > upper else lower
                if observed > upper or observed < lower:
                    deviation_ratio = abs(observed - mean) / stddev if stddev > DECIMAL_ZERO else None
                else:
                    continue
            else:
                upper = max(reference * self.sparse_lead_multiplier_high, reference + self.sparse_lead_abs_diff_threshold)
                lower = max(DECIMAL_ZERO, reference * self.sparse_lead_multiplier_low)
                if observed > upper or observed < lower:
                    sparse_trigger = True
                    threshold = upper if observed > upper else lower
                else:
                    continue

            repeats = self._repeat_occurrence_count(
                db,
                entity_type="quote_outcome",
                entity_id=row.id,
                metric_name="quoted_lead_time",
                anomaly_type="lead_time_outlier",
            )
            severity = self._lead_time_severity(
                deviation_ratio=deviation_ratio,
                sample_size=baseline.sample_size,
                repeats=repeats,
                sparse_trigger=sparse_trigger,
            )
            detected.append(self._persist_flag(
                db,
                entity_type="quote_outcome",
                entity_id=row.id,
                metric_name="quoted_lead_time",
                observed_value=observed,
                threshold_value=threshold,
                anomaly_type="lead_time_outlier",
                severity=severity,
                detected_at=datetime.combine(row.quote_date, datetime.min.time(), tzinfo=timezone.utc) if row.quote_date else row.created_at,
                source_context_json={
                    "baseline_reference": str(reference),
                    "baseline_sample_size": baseline.sample_size,
                    "vendor_id": row.vendor_id,
                    "bom_line_id": row.bom_line_id,
                    "source_tables": ["pricing.quote_outcomes", "pricing.lead_time_history", "pricing.vendor_performance"],
                },
            ))

        history_query = db.query(LeadTimeHistory).filter(LeadTimeHistory.lead_time_diff_days.is_not(None))
        if lead_time_history_ids:
            history_query = history_query.filter(LeadTimeHistory.id.in_(list(lead_time_history_ids)))
        if vendor_id:
            history_query = history_query.filter(LeadTimeHistory.vendor_id == vendor_id)
        if bom_line_id:
            history_query = history_query.filter(LeadTimeHistory.bom_line_id == bom_line_id)

        for row in history_query.all():
            observed = self._to_decimal(row.lead_time_diff_days)
            if observed is None:
                continue
            baseline = self._lead_time_diff_baseline(db, row)
            reference = baseline.reference_value
            values = baseline.values
            stddev = self._population_stddev(values)
            threshold = None
            sparse_trigger = False
            deviation_ratio = None

            if baseline.sample_size >= 3 and reference is not None and stddev is not None and stddev > DECIMAL_ZERO:
                upper = reference + (DECIMAL_TWO * stddev)
                lower = reference - (DECIMAL_TWO * stddev)
                threshold = upper if observed > upper else lower
                if observed > upper or observed < lower:
                    deviation_ratio = abs(observed - reference) / stddev
                else:
                    continue
            else:
                if abs(observed) > self.sparse_lead_abs_diff_threshold:
                    sparse_trigger = True
                    threshold = self.sparse_lead_abs_diff_threshold
                else:
                    continue

            repeats = self._repeat_occurrence_count(
                db,
                entity_type="lead_time_history",
                entity_id=row.id,
                metric_name="lead_time_diff_days",
                anomaly_type="lead_time_diff_outlier",
            )
            severity = self._lead_time_severity(
                deviation_ratio=deviation_ratio,
                sample_size=baseline.sample_size,
                repeats=repeats,
                sparse_trigger=sparse_trigger,
            )
            detected.append(self._persist_flag(
                db,
                entity_type="lead_time_history",
                entity_id=row.id,
                metric_name="lead_time_diff_days",
                observed_value=observed,
                threshold_value=threshold,
                anomaly_type="lead_time_diff_outlier",
                severity=severity,
                detected_at=row.recorded_at,
                source_context_json={
                    "baseline_mean": str(reference) if reference is not None else None,
                    "baseline_sample_size": baseline.sample_size,
                    "vendor_id": row.vendor_id,
                    "bom_line_id": row.bom_line_id,
                    "source_tables": ["pricing.lead_time_history"],
                },
            ))

        return detected

    def _status_bucket(self, status: str | None) -> str:
        status_norm = (status or "UNKNOWN").upper()
        if status_norm in OUT_OF_STOCK_STATUSES:
            return "out"
        if status_norm in IN_STOCK_STATUSES:
            return "in"
        return "other"

    def _find_previous_sku_availability(self, db: Session, row: SKUAvailabilitySnapshot) -> SKUAvailabilitySnapshot | None:
        query = db.query(SKUAvailabilitySnapshot).filter(SKUAvailabilitySnapshot.sku_offer_id == row.sku_offer_id)
        if row.inventory_location is None:
            query = query.filter(SKUAvailabilitySnapshot.inventory_location.is_(None))
        else:
            query = query.filter(SKUAvailabilitySnapshot.inventory_location == row.inventory_location)
        return (
            query
            .filter(SKUAvailabilitySnapshot.id != row.id, SKUAvailabilitySnapshot.snapshot_at < row.snapshot_at)
            .order_by(SKUAvailabilitySnapshot.snapshot_at.desc())
            .first()
        )

    def _find_previous_canonical_availability(self, db: Session, row: CanonicalAvailabilitySnapshot) -> CanonicalAvailabilitySnapshot | None:
        query = db.query(CanonicalAvailabilitySnapshot).filter(CanonicalAvailabilitySnapshot.canonical_sku_id == row.canonical_sku_id)
        if row.inventory_location is None:
            query = query.filter(CanonicalAvailabilitySnapshot.inventory_location.is_(None))
        else:
            query = query.filter(CanonicalAvailabilitySnapshot.inventory_location == row.inventory_location)
        return (
            query
            .filter(CanonicalAvailabilitySnapshot.id != row.id, CanonicalAvailabilitySnapshot.snapshot_at < row.snapshot_at)
            .order_by(CanonicalAvailabilitySnapshot.snapshot_at.desc())
            .first()
        )

    def detect_availability_anomalies(
        self,
        db: Session,
        *,
        sku_availability_snapshot_ids: Iterable[str] | None = None,
        canonical_availability_snapshot_ids: Iterable[str] | None = None,
        sku_offer_id: str | None = None,
        canonical_sku_id: str | None = None,
    ) -> list[AnomalyFlag]:
        detected: list[AnomalyFlag] = []

        sku_query = db.query(SKUAvailabilitySnapshot)
        if sku_availability_snapshot_ids:
            sku_query = sku_query.filter(SKUAvailabilitySnapshot.id.in_(list(sku_availability_snapshot_ids)))
        if sku_offer_id:
            sku_query = sku_query.filter(SKUAvailabilitySnapshot.sku_offer_id == sku_offer_id)

        for row in sku_query.all():
            observed_qty = self._to_decimal(row.available_qty)
            if observed_qty is not None and (observed_qty < DECIMAL_ZERO or observed_qty > DECIMAL_ONE_MILLION):
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="sku_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    anomaly_type="invalid_stock_value",
                )
                detected.append(self._persist_flag(
                    db,
                    entity_type="sku_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    observed_value=observed_qty,
                    threshold_value=DECIMAL_ZERO if observed_qty < DECIMAL_ZERO else DECIMAL_ONE_MILLION,
                    anomaly_type="invalid_stock_value",
                    severity=self._availability_severity(qty_ratio=None, repeats=repeats, invalid=True),
                    detected_at=row.snapshot_at,
                    source_context_json={
                        "availability_status": row.availability_status,
                        "inventory_location": row.inventory_location,
                        "source_tables": ["market.sku_availability_snapshots"],
                    },
                ))
                continue

            previous = self._find_previous_sku_availability(db, row)
            if previous is None:
                continue

            previous_qty = self._to_decimal(previous.available_qty) or DECIMAL_ZERO
            current_qty = observed_qty or DECIMAL_ZERO
            previous_bucket = self._status_bucket(previous.availability_status)
            current_bucket = self._status_bucket(row.availability_status)
            large_jump_threshold = max(DECIMAL_ONE_THOUSAND, previous_qty * Decimal("10"), (self._to_decimal(row.moq) or DECIMAL_ZERO) * Decimal("10"))

            if previous_bucket == "out" and current_bucket == "in" and current_qty >= large_jump_threshold:
                ratio = (current_qty / max(previous_qty, Decimal("1"))) if current_qty > DECIMAL_ZERO else None
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="sku_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    anomaly_type="availability_jump",
                )
                detected.append(self._persist_flag(
                    db,
                    entity_type="sku_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    observed_value=current_qty,
                    threshold_value=large_jump_threshold,
                    anomaly_type="availability_jump",
                    severity=self._availability_severity(qty_ratio=ratio, repeats=repeats),
                    detected_at=row.snapshot_at,
                    source_context_json={
                        "previous_snapshot_id": previous.id,
                        "previous_status": previous.availability_status,
                        "previous_available_qty": str(previous_qty),
                        "inventory_location": row.inventory_location,
                        "source_tables": ["market.sku_availability_snapshots"],
                    },
                ))
            elif previous_bucket != current_bucket and row.snapshot_at - previous.snapshot_at <= self.contradiction_window:
                qty_delta = abs(current_qty - previous_qty)
                threshold = max(Decimal("500"), (self._to_decimal(row.moq) or DECIMAL_ZERO) * Decimal("5"))
                if qty_delta >= threshold:
                    repeats = self._repeat_occurrence_count(
                        db,
                        entity_type="sku_availability_snapshot",
                        entity_id=row.id,
                        metric_name="availability_status",
                        anomaly_type="availability_contradiction",
                    )
                    detected.append(self._persist_flag(
                        db,
                        entity_type="sku_availability_snapshot",
                        entity_id=row.id,
                        metric_name="availability_status",
                        observed_value=current_qty,
                        threshold_value=threshold,
                        anomaly_type="availability_contradiction",
                        severity=self._availability_severity(qty_ratio=(qty_delta / threshold) if threshold > DECIMAL_ZERO else None, repeats=repeats),
                        detected_at=row.snapshot_at,
                        source_context_json={
                            "previous_snapshot_id": previous.id,
                            "previous_status": previous.availability_status,
                            "previous_available_qty": str(previous_qty),
                            "inventory_location": row.inventory_location,
                            "source_tables": ["market.sku_availability_snapshots"],
                        },
                    ))

        canonical_query = db.query(CanonicalAvailabilitySnapshot)
        if canonical_availability_snapshot_ids:
            canonical_query = canonical_query.filter(CanonicalAvailabilitySnapshot.id.in_(list(canonical_availability_snapshot_ids)))
        if canonical_sku_id:
            canonical_query = canonical_query.filter(CanonicalAvailabilitySnapshot.canonical_sku_id == canonical_sku_id)

        for row in canonical_query.all():
            observed_qty = self._to_decimal(row.available_qty)
            if observed_qty is not None and (observed_qty < DECIMAL_ZERO or observed_qty > DECIMAL_ONE_MILLION):
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="canonical_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    anomaly_type="invalid_stock_value",
                )
                detected.append(self._persist_flag(
                    db,
                    entity_type="canonical_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    observed_value=observed_qty,
                    threshold_value=DECIMAL_ZERO if observed_qty < DECIMAL_ZERO else DECIMAL_ONE_MILLION,
                    anomaly_type="invalid_stock_value",
                    severity=self._availability_severity(qty_ratio=None, repeats=repeats, invalid=True),
                    detected_at=row.snapshot_at,
                    source_context_json={
                        "availability_status": row.availability_status,
                        "inventory_location": row.inventory_location,
                        "source_tables": ["market.canonical_availability_snapshot"],
                    },
                ))
                continue

            previous = self._find_previous_canonical_availability(db, row)
            if previous is None:
                continue

            previous_qty = self._to_decimal(previous.available_qty) or DECIMAL_ZERO
            current_qty = observed_qty or DECIMAL_ZERO
            previous_bucket = self._status_bucket(previous.availability_status)
            current_bucket = self._status_bucket(row.availability_status)
            large_jump_threshold = max(DECIMAL_ONE_THOUSAND, previous_qty * Decimal("10"), (self._to_decimal(row.moq) or DECIMAL_ZERO) * Decimal("10"))

            if previous_bucket == "out" and current_bucket == "in" and current_qty >= large_jump_threshold:
                ratio = (current_qty / max(previous_qty, Decimal("1"))) if current_qty > DECIMAL_ZERO else None
                repeats = self._repeat_occurrence_count(
                    db,
                    entity_type="canonical_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    anomaly_type="availability_jump",
                )
                detected.append(self._persist_flag(
                    db,
                    entity_type="canonical_availability_snapshot",
                    entity_id=row.id,
                    metric_name="available_qty",
                    observed_value=current_qty,
                    threshold_value=large_jump_threshold,
                    anomaly_type="availability_jump",
                    severity=self._availability_severity(qty_ratio=ratio, repeats=repeats),
                    detected_at=row.snapshot_at,
                    source_context_json={
                        "previous_snapshot_id": previous.id,
                        "previous_status": previous.availability_status,
                        "previous_available_qty": str(previous_qty),
                        "inventory_location": row.inventory_location,
                        "source_tables": ["market.canonical_availability_snapshot"],
                    },
                ))
            elif previous_bucket != current_bucket and row.snapshot_at - previous.snapshot_at <= self.contradiction_window:
                qty_delta = abs(current_qty - previous_qty)
                threshold = max(Decimal("500"), (self._to_decimal(row.moq) or DECIMAL_ZERO) * Decimal("5"))
                if qty_delta >= threshold:
                    repeats = self._repeat_occurrence_count(
                        db,
                        entity_type="canonical_availability_snapshot",
                        entity_id=row.id,
                        metric_name="availability_status",
                        anomaly_type="availability_contradiction",
                    )
                    detected.append(self._persist_flag(
                        db,
                        entity_type="canonical_availability_snapshot",
                        entity_id=row.id,
                        metric_name="availability_status",
                        observed_value=current_qty,
                        threshold_value=threshold,
                        anomaly_type="availability_contradiction",
                        severity=self._availability_severity(qty_ratio=(qty_delta / threshold) if threshold > DECIMAL_ZERO else None, repeats=repeats),
                        detected_at=row.snapshot_at,
                        source_context_json={
                            "previous_snapshot_id": previous.id,
                            "previous_status": previous.availability_status,
                            "previous_available_qty": str(previous_qty),
                            "inventory_location": row.inventory_location,
                            "source_tables": ["market.canonical_availability_snapshot"],
                        },
                    ))

        return detected

    def detect_for_entity(self, db: Session, *, entity_type: str, entity_id: str) -> list[AnomalyFlag]:
        if entity_type == "quote_outcome":
            return self.detect_price_anomalies(db, quote_outcome_ids=[entity_id]) + self.detect_lead_time_anomalies(db, quote_outcome_ids=[entity_id])
        if entity_type == "lead_time_history":
            return self.detect_lead_time_anomalies(db, lead_time_history_ids=[entity_id])
        if entity_type == "canonical_offer_snapshot":
            return self.detect_price_anomalies(db, canonical_offer_snapshot_ids=[entity_id])
        if entity_type == "sku_offer_price_break":
            return self.detect_price_anomalies(db, sku_offer_price_break_ids=[entity_id])
        if entity_type == "sku_availability_snapshot":
            return self.detect_availability_anomalies(db, sku_availability_snapshot_ids=[entity_id])
        if entity_type == "canonical_availability_snapshot":
            return self.detect_availability_anomalies(db, canonical_availability_snapshot_ids=[entity_id])
        raise ValueError(f"Unsupported anomaly entity_type: {entity_type}")

    def detect_batch(
        self,
        db: Session,
        *,
        include_price: bool = True,
        include_lead_time: bool = True,
        include_availability: bool = True,
    ) -> list[AnomalyFlag]:
        detected: list[AnomalyFlag] = []
        if include_price:
            detected.extend(self.detect_price_anomalies(db))
        if include_lead_time:
            detected.extend(self.detect_lead_time_anomalies(db))
        if include_availability:
            detected.extend(self.detect_availability_anomalies(db))
        return detected

    def get_anomaly_flags(
        self,
        db: Session,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[AnomalyFlag]:
        query = db.query(AnomalyFlag)
        if entity_type:
            query = query.filter(AnomalyFlag.entity_type == entity_type)
        if entity_id:
            query = query.filter(AnomalyFlag.entity_id == entity_id)
        if severity:
            query = query.filter(AnomalyFlag.severity == severity)
        return query.order_by(AnomalyFlag.detected_at.desc(), AnomalyFlag.created_at.desc()).limit(limit).all()


anomaly_detection_service = AnomalyDetectionService()