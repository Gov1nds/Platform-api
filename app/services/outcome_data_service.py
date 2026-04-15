from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.outcomes import LeadTimeHistory, OverrideEvent, QuoteOutcome, VendorPerformance
from app.models.user import User
from app.models.vendor import Vendor


DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_RATE_PLACES = Decimal("0.000001")
DECIMAL_LEAD_TIME_PLACES = Decimal("0.01")
DECIMAL_PRICE_VARIANCE_PLACES = Decimal("0.00000001")
DECIMAL_VARIANCE_PLACES = Decimal("0.0001")


class OutcomeDataService:
    """Phase 2C outcome ingestion, lead-time intelligence, and vendor performance aggregation."""

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _require_bom_line(self, db: Session, bom_line_id: str) -> BOMPart:
        row = db.query(BOMPart).filter(BOMPart.id == bom_line_id, BOMPart.deleted_at.is_(None)).first()
        if not row:
            raise ValueError(f"Unknown BOM line: {bom_line_id}")
        return row

    def _require_vendor(self, db: Session, vendor_id: str) -> Vendor:
        row = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.deleted_at.is_(None)).first()
        if not row:
            raise ValueError(f"Unknown vendor: {vendor_id}")
        return row

    def _require_user(self, db: Session, user_id: str | None) -> User | None:
        if not user_id:
            return None
        row = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
        if not row:
            raise ValueError(f"Unknown user: {user_id}")
        return row

    def _to_decimal(self, value: Decimal | float | int | str | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    def _is_valid_duration_window(self, order_date: date | None, delivery_date: date | None) -> bool:
        if not order_date or not delivery_date:
            return False
        if delivery_date < order_date:
            return False
        return True

    def _compute_actual_lead_time_days(self, order_date: date | None, delivery_date: date | None) -> Decimal | None:
        if not self._is_valid_duration_window(order_date, delivery_date):
            return None
        delta = delivery_date - order_date
        if delta.days < 0:
            return None
        return Decimal(delta.days)

    def _population_variance(self, values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        mean = sum(values) / Decimal(len(values))
        variance = sum((value - mean) * (value - mean) for value in values) / Decimal(len(values))
        return variance.quantize(DECIMAL_VARIANCE_PLACES)

    def ingest_quote_outcome(
        self,
        db: Session,
        *,
        bom_line_id: str,
        vendor_id: str,
        quoted_price: Decimal | float | str | None = None,
        quoted_lead_time: Decimal | float | str | None = None,
        is_accepted: bool = False,
        accepted_price: Decimal | float | str | None = None,
        accepted_lead_time: Decimal | float | str | None = None,
        quote_date: date | None = None,
        order_date: date | None = None,
        delivery_date: date | None = None,
        issues_flag: bool = False,
        source_metadata: dict | None = None,
    ) -> QuoteOutcome:
        self._require_bom_line(db, bom_line_id)
        self._require_vendor(db, vendor_id)

        row = QuoteOutcome(
            bom_line_id=bom_line_id,
            vendor_id=vendor_id,
            quoted_price=quoted_price,
            quoted_lead_time=quoted_lead_time,
            is_accepted=bool(is_accepted),
            accepted_price=accepted_price,
            accepted_lead_time=accepted_lead_time,
            quote_date=quote_date,
            order_date=order_date,
            delivery_date=delivery_date,
            issues_flag=bool(issues_flag),
            source_metadata=source_metadata or {},
        )
        db.add(row)
        db.flush()
        return row

    def log_override_event(
        self,
        db: Session,
        *,
        event_id: str,
        user_id: str | None,
        bom_line_id: str,
        recommended_vendor_id: str | None,
        chosen_vendor_id: str | None,
        override_reason_code: str,
        timestamp: datetime | None = None,
        source_metadata: dict | None = None,
    ) -> OverrideEvent:
        self._require_user(db, user_id)
        self._require_bom_line(db, bom_line_id)
        if recommended_vendor_id:
            self._require_vendor(db, recommended_vendor_id)
        if chosen_vendor_id:
            self._require_vendor(db, chosen_vendor_id)

        row = OverrideEvent(
            event_id=event_id,
            user_id=user_id,
            bom_line_id=bom_line_id,
            recommended_vendor_id=recommended_vendor_id,
            chosen_vendor_id=chosen_vendor_id,
            override_reason_code=override_reason_code,
            timestamp=timestamp or self._utcnow(),
            source_metadata=source_metadata or {},
        )
        db.add(row)
        db.flush()
        return row

    def record_lead_time_history_for_outcome(
        self,
        db: Session,
        *,
        quote_outcome_id: str,
        recorded_at: datetime | None = None,
    ) -> LeadTimeHistory | None:
        outcome = db.query(QuoteOutcome).filter(QuoteOutcome.id == quote_outcome_id).first()
        if not outcome:
            raise ValueError(f"Unknown quote outcome: {quote_outcome_id}")

        existing = (
            db.query(LeadTimeHistory)
            .filter(LeadTimeHistory.quote_outcome_id == quote_outcome_id)
            .first()
        )
        if existing:
            return existing

        actual_lead_time = self._compute_actual_lead_time_days(outcome.order_date, outcome.delivery_date)
        if actual_lead_time is None:
            return None

        quoted_lead_time = self._to_decimal(outcome.quoted_lead_time)
        lead_time_diff = None
        if quoted_lead_time is not None:
            lead_time_diff = actual_lead_time - quoted_lead_time

        row = LeadTimeHistory(
            quote_outcome_id=outcome.id,
            vendor_id=outcome.vendor_id,
            bom_line_id=outcome.bom_line_id,
            quoted_lead_time=quoted_lead_time,
            actual_lead_time=actual_lead_time,
            lead_time_diff_days=lead_time_diff,
            recorded_at=recorded_at or self._utcnow(),
            source_metadata={
                "quote_outcome_id": outcome.id,
                "quote_date": outcome.quote_date.isoformat() if outcome.quote_date else None,
                "order_date": outcome.order_date.isoformat() if outcome.order_date else None,
                "delivery_date": outcome.delivery_date.isoformat() if outcome.delivery_date else None,
                "aggregation_version": "phase2c_batch2c2",
            },
        )
        db.add(row)
        db.flush()
        return row

    def sync_lead_time_history(
        self,
        db: Session,
        *,
        vendor_ids: Iterable[str] | None = None,
        quote_outcome_ids: Iterable[str] | None = None,
    ) -> list[LeadTimeHistory]:
        query = db.query(QuoteOutcome).filter(
            QuoteOutcome.order_date.is_not(None),
            QuoteOutcome.delivery_date.is_not(None),
        )
        if vendor_ids:
            query = query.filter(QuoteOutcome.vendor_id.in_(list(vendor_ids)))
        if quote_outcome_ids:
            query = query.filter(QuoteOutcome.id.in_(list(quote_outcome_ids)))

        created_or_existing: list[LeadTimeHistory] = []
        for outcome in query.all():
            row = self.record_lead_time_history_for_outcome(db, quote_outcome_id=outcome.id)
            if row is not None:
                created_or_existing.append(row)
        return created_or_existing

    def rebuild_vendor_performance(
        self,
        db: Session,
        *,
        period_start: date,
        period_end: date,
        vendor_ids: Iterable[str] | None = None,
        replace_existing: bool = True,
    ) -> list[VendorPerformance]:
        if period_end < period_start:
            raise ValueError("period_end must be on or after period_start")

        self.sync_lead_time_history(db, vendor_ids=vendor_ids)

        outcome_query = db.query(QuoteOutcome).filter(
            QuoteOutcome.quote_date.is_not(None),
            QuoteOutcome.quote_date >= period_start,
            QuoteOutcome.quote_date <= period_end,
        )
        history_query = db.query(LeadTimeHistory).filter(
            LeadTimeHistory.recorded_at >= datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc),
            LeadTimeHistory.recorded_at < datetime.combine(period_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
        )
        if vendor_ids:
            vendor_ids_list = list(vendor_ids)
            outcome_query = outcome_query.filter(QuoteOutcome.vendor_id.in_(vendor_ids_list))
            history_query = history_query.filter(LeadTimeHistory.vendor_id.in_(vendor_ids_list))
        else:
            vendor_ids_list = None

        outcomes = outcome_query.all()
        histories = history_query.all()

        outcome_bucket: dict[str, list[QuoteOutcome]] = {}
        for row in outcomes:
            outcome_bucket.setdefault(row.vendor_id, []).append(row)

        history_bucket: dict[str, list[LeadTimeHistory]] = {}
        for row in histories:
            history_bucket.setdefault(row.vendor_id, []).append(row)

        all_vendor_ids = set(outcome_bucket.keys()) | set(history_bucket.keys())

        if replace_existing:
            delete_q = db.query(VendorPerformance).filter(
                VendorPerformance.period_start == period_start,
                VendorPerformance.period_end == period_end,
            )
            if vendor_ids_list:
                delete_q = delete_q.filter(VendorPerformance.vendor_id.in_(vendor_ids_list))
            delete_q.delete(synchronize_session=False)
            db.flush()

        created: list[VendorPerformance] = []
        for vendor_id in all_vendor_ids:
            rows = outcome_bucket.get(vendor_id, [])
            lead_rows = history_bucket.get(vendor_id, [])

            total_quotes = len(rows)
            wins = sum(1 for row in rows if row.is_accepted)

            actual_lead_times = [self._to_decimal(row.actual_lead_time) for row in lead_rows if row.actual_lead_time is not None]
            actual_lead_times = [row for row in actual_lead_times if row is not None]
            lead_diffs = [self._to_decimal(row.lead_time_diff_days) for row in lead_rows if row.lead_time_diff_days is not None]
            lead_diffs = [row for row in lead_diffs if row is not None]
            on_time_flags = [DECIMAL_ONE if row.lead_time_diff_days is not None and self._to_decimal(row.lead_time_diff_days) <= DECIMAL_ZERO else DECIMAL_ZERO for row in lead_rows if row.lead_time_diff_days is not None]

            quoted_prices = [self._to_decimal(row.quoted_price) for row in rows if row.quoted_price is not None]
            quoted_prices = [row for row in quoted_prices if row is not None]
            average_quote_price = (sum(quoted_prices) / Decimal(len(quoted_prices))) if quoted_prices else None
            price_deviations = []
            if average_quote_price is not None:
                for quoted_price in quoted_prices:
                    price_deviations.append(quoted_price - average_quote_price)
            avg_price_variance = ((sum(price_deviations) / Decimal(len(price_deviations))).quantize(DECIMAL_PRICE_VARIANCE_PLACES) if price_deviations else None)

            avg_lead_time = ((sum(actual_lead_times) / Decimal(len(actual_lead_times))).quantize(DECIMAL_LEAD_TIME_PLACES) if actual_lead_times else None)
            on_time_rate = ((sum(on_time_flags) / Decimal(len(on_time_flags))).quantize(DECIMAL_RATE_PLACES) if on_time_flags else None)
            win_rate = ((Decimal(wins) / Decimal(total_quotes)).quantize(DECIMAL_RATE_PLACES) if total_quotes else None)
            lead_time_variance = self._population_variance(lead_diffs)

            perf = VendorPerformance(
                vendor_id=vendor_id,
                period_start=period_start,
                period_end=period_end,
                on_time_rate=on_time_rate,
                avg_lead_time=avg_lead_time,
                lead_time_variance=lead_time_variance,
                price_variance=avg_price_variance,
                po_win_rate=win_rate,
                source_metadata={
                    "quote_outcome_count": total_quotes,
                    "accepted_outcome_count": wins,
                    "lead_time_history_count": len(lead_rows),
                    "aggregation_version": "phase2c_batch2c2",
                },
            )
            db.add(perf)
            created.append(perf)

        db.flush()
        return created

    def get_vendor_performance(
        self,
        db: Session,
        *,
        vendor_id: str,
    ) -> VendorPerformance | None:
        self._require_vendor(db, vendor_id)
        return (
            db.query(VendorPerformance)
            .filter(VendorPerformance.vendor_id == vendor_id)
            .order_by(VendorPerformance.period_end.desc(), VendorPerformance.created_at.desc())
            .first()
        )

    def get_adjusted_lead_time(
        self,
        db: Session,
        *,
        vendor_id: str,
        bom_line_id: str,
    ) -> Decimal | None:
        self._require_vendor(db, vendor_id)
        self._require_bom_line(db, bom_line_id)

        base_outcome = (
            db.query(QuoteOutcome)
            .filter(
                QuoteOutcome.vendor_id == vendor_id,
                QuoteOutcome.bom_line_id == bom_line_id,
                QuoteOutcome.quoted_lead_time.is_not(None),
            )
            .order_by(
                QuoteOutcome.quote_date.desc().nullslast(),
                QuoteOutcome.created_at.desc(),
            )
            .first()
        )
        if not base_outcome:
            return None

        base_quoted_lead_time = self._to_decimal(base_outcome.quoted_lead_time)
        if base_quoted_lead_time is None:
            return None

        lead_rows = (
            db.query(LeadTimeHistory)
            .filter(
                LeadTimeHistory.vendor_id == vendor_id,
                LeadTimeHistory.lead_time_diff_days.is_not(None),
            )
            .order_by(LeadTimeHistory.recorded_at.desc())
            .all()
        )
        diffs = [self._to_decimal(row.lead_time_diff_days) for row in lead_rows if row.lead_time_diff_days is not None]
        diffs = [row for row in diffs if row is not None]
        if not diffs:
            return base_quoted_lead_time

        avg_diff = (sum(diffs) / Decimal(len(diffs))).quantize(DECIMAL_LEAD_TIME_PLACES)
        adjusted = base_quoted_lead_time + avg_diff
        if adjusted < DECIMAL_ZERO:
            return DECIMAL_ZERO
        return adjusted


outcome_data_service = OutcomeDataService()