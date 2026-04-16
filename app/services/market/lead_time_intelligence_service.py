"""
Lead-Time Intelligence service (Phase 3).

Implements Execution Plan §4 "Lead-Time Intel: typical lead-time
distributions per vendor-category from past orders."

Provides distribution stats (mean, median, p90), an actual-vs-quoted
deviation recorder, and a reliability score per (vendor, category).

Note: This Phase-3 service is intentionally separate from the existing
app.services.lead_time_intelligence_service which is a facade over the
outcome_data_service. The two are complementary: the Phase-3 service
works against pricing.vendor_lead_time_history (Phase-3 table), while
the Phase-2c service works against pricing.lead_time_history.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.market_intelligence import VendorLeadTimeHistoryPhase3
from app.models.vendor import VendorLeadTimeBand

logger = logging.getLogger(__name__)


@dataclass
class LeadTimeDistribution:
    sample_size: int
    mean: float | None = None
    median: float | None = None
    std_dev: float | None = None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    confidence: float = 0.0
    category_tag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "mean": self.mean,
            "median": self.median,
            "std_dev": self.std_dev,
            "p25": self.p25,
            "p75": self.p75,
            "p90": self.p90,
            "confidence": round(self.confidence, 4),
            "category_tag": self.category_tag,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class LeadTimeIntelligencePhase3Service:
    """Vendor lead-time distribution builder + reliability scorer."""

    def get_lead_time_distribution(
        self,
        vendor_id: str,
        category_tag: str | None,
        db: Session,
    ) -> LeadTimeDistribution:
        q = db.query(VendorLeadTimeHistoryPhase3).filter(
            VendorLeadTimeHistoryPhase3.vendor_id == vendor_id,
            VendorLeadTimeHistoryPhase3.actual_lead_time_days.isnot(None),
        )
        if category_tag:
            q = q.filter(VendorLeadTimeHistoryPhase3.category_tag == category_tag)
        rows = q.order_by(VendorLeadTimeHistoryPhase3.recorded_at.desc()).limit(200).all()

        values = [
            _as_float(r.actual_lead_time_days)
            for r in rows
            if r.actual_lead_time_days is not None
        ]
        n = len(values)
        dist = LeadTimeDistribution(sample_size=n, category_tag=category_tag)
        if n == 0:
            dist.confidence = 0.0
            return dist

        dist.mean = round(sum(values) / n, 3)
        dist.median = round(statistics.median(values), 3)
        dist.std_dev = round(statistics.stdev(values), 3) if n >= 2 else 0.0
        dist.p25 = round(_percentile(values, 25.0), 3)
        dist.p75 = round(_percentile(values, 75.0), 3)
        dist.p90 = round(_percentile(values, 90.0), 3)

        # Confidence scales with sample size
        if n >= 20:
            dist.confidence = 0.90
        elif n >= 10:
            dist.confidence = 0.75
        elif n >= 5:
            dist.confidence = 0.55
        elif n >= 3:
            dist.confidence = 0.40
        else:
            dist.confidence = 0.25
        return dist

    def record_actual_lead_time(
        self,
        vendor_id: str,
        category_tag: str | None,
        actual_days: float | int | Decimal,
        quoted_days: float | int | Decimal | None,
        source_po_id: str | None,
        db: Session,
        material_family: str | None = None,
        source_rfq_id: str | None = None,
    ) -> VendorLeadTimeHistoryPhase3:
        actual = Decimal(str(actual_days))
        quoted = Decimal(str(quoted_days)) if quoted_days is not None else None
        deviation = (actual - quoted) if quoted is not None else None

        row = VendorLeadTimeHistoryPhase3(
            vendor_id=vendor_id,
            category_tag=category_tag,
            material_family=material_family,
            actual_lead_time_days=actual,
            quoted_lead_time_days=quoted,
            deviation_days=deviation,
            source_rfq_id=source_rfq_id,
            source_po_id=source_po_id,
            recorded_at=_now().date(),
        )
        db.add(row)
        db.flush()

        # Update VendorLeadTimeBand typical_days if enough history
        dist = self.get_lead_time_distribution(vendor_id, category_tag, db)
        if dist.sample_size >= 3 and dist.median is not None:
            band = (
                db.query(VendorLeadTimeBand)
                .filter(
                    VendorLeadTimeBand.vendor_id == vendor_id,
                    VendorLeadTimeBand.category_tag == category_tag,
                )
                .first()
            )
            if band is None:
                band = VendorLeadTimeBand(
                    vendor_id=vendor_id,
                    category_tag=category_tag,
                    material_family=material_family,
                    source="derived_from_history",
                )
                db.add(band)
            band.lead_time_typical_days = Decimal(str(dist.median))
            if dist.p25 is not None:
                band.lead_time_min_days = int(round(dist.p25))
            if dist.p90 is not None:
                band.lead_time_max_days = int(round(dist.p90))
            band.confidence = Decimal(str(dist.confidence))
        return row

    def get_lead_time_reliability_score(
        self,
        vendor_id: str,
        category_tag: str | None,
        db: Session,
    ) -> float:
        """Fraction of past orders delivered within quoted lead time."""
        q = db.query(VendorLeadTimeHistoryPhase3).filter(
            VendorLeadTimeHistoryPhase3.vendor_id == vendor_id,
            VendorLeadTimeHistoryPhase3.actual_lead_time_days.isnot(None),
            VendorLeadTimeHistoryPhase3.quoted_lead_time_days.isnot(None),
        )
        if category_tag:
            q = q.filter(VendorLeadTimeHistoryPhase3.category_tag == category_tag)
        rows = q.all()
        if not rows:
            return 0.5  # no data → neutral prior
        on_time = sum(
            1 for r in rows
            if _as_float(r.actual_lead_time_days) <= _as_float(r.quoted_lead_time_days) + 1.0
        )
        return round(on_time / len(rows), 4)


lead_time_intelligence_phase3_service = LeadTimeIntelligencePhase3Service()
