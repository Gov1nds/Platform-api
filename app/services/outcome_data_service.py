from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.bom import BOMPart
from app.models.outcomes import OverrideEvent, QuoteOutcome, VendorPerformance
from app.models.user import User
from app.models.vendor import Vendor


class OutcomeDataService:
    """Phase 2C Batch 2C.1 append-only outcome ingestion and scorecard foundation."""

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

        query = db.query(QuoteOutcome).filter(
            QuoteOutcome.quote_date.is_not(None),
            QuoteOutcome.quote_date >= period_start,
            QuoteOutcome.quote_date <= period_end,
        )
        if vendor_ids:
            query = query.filter(QuoteOutcome.vendor_id.in_(list(vendor_ids)))

        outcomes = query.all()
        bucket: dict[str, list[QuoteOutcome]] = {}
        for row in outcomes:
            bucket.setdefault(row.vendor_id, []).append(row)

        if replace_existing:
            delete_q = db.query(VendorPerformance).filter(
                VendorPerformance.period_start == period_start,
                VendorPerformance.period_end == period_end,
            )
            if vendor_ids:
                delete_q = delete_q.filter(VendorPerformance.vendor_id.in_(list(vendor_ids)))
            delete_q.delete(synchronize_session=False)
            db.flush()

        created: list[VendorPerformance] = []
        for vendor_id, rows in bucket.items():
            total_quotes = len(rows)
            wins = sum(1 for row in rows if row.is_accepted)

            lead_times = []
            price_variances = []
            on_time_flags = []
            for row in rows:
                lead = row.accepted_lead_time if row.is_accepted and row.accepted_lead_time is not None else row.quoted_lead_time
                if lead is not None:
                    lead_times.append(Decimal(str(lead)))

                if row.is_accepted and row.quoted_price is not None and row.accepted_price is not None:
                    price_variances.append(Decimal(str(row.accepted_price)) - Decimal(str(row.quoted_price)))

                if row.is_accepted and row.order_date and row.delivery_date:
                    baseline = row.accepted_lead_time if row.accepted_lead_time is not None else row.quoted_lead_time
                    if baseline is not None:
                        actual_days = Decimal((row.delivery_date - row.order_date).days)
                        on_time_flags.append(Decimal("1") if actual_days <= Decimal(str(baseline)) else Decimal("0"))

            avg_lead_time = (sum(lead_times) / Decimal(len(lead_times))) if lead_times else None
            avg_price_variance = (sum(price_variances) / Decimal(len(price_variances))) if price_variances else None
            on_time_rate = (sum(on_time_flags) / Decimal(len(on_time_flags))) if on_time_flags else None
            win_rate = (Decimal(wins) / Decimal(total_quotes)) if total_quotes else None

            perf = VendorPerformance(
                vendor_id=vendor_id,
                period_start=period_start,
                period_end=period_end,
                on_time_rate=on_time_rate,
                avg_lead_time=avg_lead_time,
                price_variance=avg_price_variance,
                po_win_rate=win_rate,
                source_metadata={
                    "quote_outcome_count": total_quotes,
                    "accepted_outcome_count": wins,
                    "aggregation_version": "phase2c_batch2c1",
                },
            )
            db.add(perf)
            created.append(perf)

        db.flush()
        return created


outcome_data_service = OutcomeDataService()