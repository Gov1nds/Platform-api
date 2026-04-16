"""
Market Anomaly Detection service.

Implements Execution Plan §4 "Anomaly Flags" and §7 "Penalize anomalies
rather than ignore them."

Checks for zero_lead_time, near_zero_price, price_spike, lead_time_spike,
impossible_moq. Persists to pricing.market_anomaly_events. Returns flag
list to caller for applying score penalties.

This Phase-3 service is complementary to the Phase-2c
anomaly_detection_service which tracks deeper statistical anomalies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.market_intelligence import MarketAnomalyEvent
from app.models.vendor import VendorLeadTimeBand

logger = logging.getLogger(__name__)


SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

SEVERITY_PENALTY = {
    SEVERITY_LOW: -0.02,
    SEVERITY_MEDIUM: -0.08,
    SEVERITY_HIGH: -0.15,
    SEVERITY_CRITICAL: -0.30,
}


@dataclass
class AnomalyFlag:
    anomaly_type: str
    severity: str
    observed: Decimal | None
    expected_low: Decimal | None
    expected_high: Decimal | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "observed": str(self.observed) if self.observed is not None else None,
            "expected_low": str(self.expected_low) if self.expected_low is not None else None,
            "expected_high": str(self.expected_high) if self.expected_high is not None else None,
            "reason": self.reason,
        }


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _now():
    return datetime.now(timezone.utc)


class MarketAnomalyService:
    """Detect outlier quotes/LT/MOQ values and persist anomaly events."""

    def check_quote_for_anomalies(
        self,
        vendor_id: str | None,
        canonical_part_key: str | None,
        quoted_price: Decimal | float | int | None,
        quoted_lead_time_days: Decimal | float | int | None,
        db: Session,
        market_median_price: Decimal | float | int | None = None,
        vendor_typical_lead_time: Decimal | float | int | None = None,
        quantity: Decimal | float | int | None = None,
    ) -> list[AnomalyFlag]:
        flags: list[AnomalyFlag] = []

        price = _as_decimal(quoted_price)
        lt = _as_decimal(quoted_lead_time_days)
        median = _as_decimal(market_median_price)
        vendor_typical_lt = _as_decimal(vendor_typical_lead_time)
        qty = _as_decimal(quantity)

        # Zero lead time → CRITICAL
        if lt is not None and lt <= 0:
            flags.append(
                AnomalyFlag(
                    anomaly_type="zero_lead_time",
                    severity=SEVERITY_CRITICAL,
                    observed=lt,
                    expected_low=Decimal("1"),
                    expected_high=None,
                    reason="quoted lead time is zero or negative — implausible",
                )
            )

        # Near-zero price (< 1% of market median) → HIGH
        if price is not None and median is not None and median > 0:
            if price < (median * Decimal("0.01")):
                flags.append(
                    AnomalyFlag(
                        anomaly_type="near_zero_price",
                        severity=SEVERITY_HIGH,
                        observed=price,
                        expected_low=median * Decimal("0.1"),
                        expected_high=median * Decimal("3.0"),
                        reason="quoted price below 1% of market median — likely data error or dumping",
                    )
                )
            elif price > (median * Decimal("3.0")):
                # Price spike → MEDIUM
                flags.append(
                    AnomalyFlag(
                        anomaly_type="price_spike",
                        severity=SEVERITY_MEDIUM,
                        observed=price,
                        expected_low=median * Decimal("0.5"),
                        expected_high=median * Decimal("2.0"),
                        reason="quoted price over 3× market median",
                    )
                )

        # Lead-time spike → HIGH
        if lt is not None and vendor_typical_lt is not None and vendor_typical_lt > 0:
            if lt > (vendor_typical_lt * Decimal("5.0")):
                flags.append(
                    AnomalyFlag(
                        anomaly_type="lead_time_spike",
                        severity=SEVERITY_HIGH,
                        observed=lt,
                        expected_low=vendor_typical_lt,
                        expected_high=vendor_typical_lt * Decimal("2"),
                        reason="quoted lead time over 5× vendor's typical",
                    )
                )

        # Impossible MOQ (qty below vendor's MOQ) → MEDIUM
        if qty is not None and vendor_id:
            min_moq = (
                db.query(VendorLeadTimeBand.moq)
                .filter(
                    VendorLeadTimeBand.vendor_id == vendor_id,
                    VendorLeadTimeBand.moq.isnot(None),
                )
                .order_by(VendorLeadTimeBand.moq.asc())
                .first()
            )
            if min_moq and min_moq[0] is not None:
                min_moq_val = _as_decimal(min_moq[0])
                if min_moq_val and qty < min_moq_val:
                    flags.append(
                        AnomalyFlag(
                            anomaly_type="impossible_moq",
                            severity=SEVERITY_MEDIUM,
                            observed=qty,
                            expected_low=min_moq_val,
                            expected_high=None,
                            reason=f"order quantity below vendor MOQ {min_moq_val}",
                        )
                    )

        # Persist events
        for flag in flags:
            db.add(
                MarketAnomalyEvent(
                    vendor_id=vendor_id,
                    canonical_part_key=canonical_part_key,
                    anomaly_type=flag.anomaly_type,
                    observed_value=flag.observed,
                    expected_range_low=flag.expected_low,
                    expected_range_high=flag.expected_high,
                    severity=flag.severity,
                    auto_flagged=True,
                    reviewed=False,
                    event_metadata={"reason": flag.reason},
                    detected_at=_now(),
                )
            )

        if flags:
            logger.info(
                "anomaly check vendor=%s part=%s flags=%d",
                vendor_id, canonical_part_key, len(flags),
            )
        return flags

    def apply_anomaly_penalty_to_score(
        self,
        base_score: float,
        anomaly_flags: list[AnomalyFlag] | list[dict[str, Any]],
    ) -> float:
        total_penalty = 0.0
        for flag in anomaly_flags:
            severity = (
                flag.severity if isinstance(flag, AnomalyFlag) else flag.get("severity")
            )
            total_penalty += SEVERITY_PENALTY.get(severity, 0.0)
        adjusted = base_score + total_penalty
        return max(0.0, min(1.0, adjusted))


market_anomaly_service = MarketAnomalyService()
