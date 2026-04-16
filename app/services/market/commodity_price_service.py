"""
Commodity Price Signal service.

Implements Execution Plan §4 "Pricing Signals" and "buy in the valley":

  - get_current_price_signal(material_family)
  - adjust_vendor_price_for_commodity_trend(...)
  - is_buy_valley(material_family)
  - ingest_commodity_signal(signal_data) — auto-computes trend + valley

All monetary math uses Decimal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.market_intelligence import CommodityPriceSignal

logger = logging.getLogger(__name__)


FRESH_DAYS = 30
STALE_DAYS = 90


@dataclass
class AdjustedPrice:
    original_price: Decimal
    adjusted_price: Decimal
    adjustment_pct: float
    adjustment_reason: str
    trend_direction: str | None
    confidence: float


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


class CommodityPriceService:
    """Commodity signal adapter serving the runtime recommendation pipeline."""

    def get_current_price_signal(
        self,
        material_family: str | None,
        db: Session,
    ) -> CommodityPriceSignal | None:
        if not material_family:
            return None
        signal = (
            db.query(CommodityPriceSignal)
            .filter(CommodityPriceSignal.material_family_tag == material_family)
            .order_by(CommodityPriceSignal.price_date.desc())
            .first()
        )
        if signal is None:
            return None
        age_days = (_today() - signal.price_date).days
        if age_days > STALE_DAYS:
            logger.info(
                "commodity signal too stale for material_family=%s age=%d",
                material_family, age_days,
            )
            return None
        return signal

    def is_buy_valley(self, material_family: str | None, db: Session) -> bool:
        signal = self.get_current_price_signal(material_family, db)
        return bool(signal and signal.is_valley)

    def adjust_vendor_price_for_commodity_trend(
        self,
        unit_price: Decimal | float | int | str,
        material_family: str | None,
        db: Session,
    ) -> AdjustedPrice:
        """
        Apply trend-aware surcharge / discount to a vendor's quoted price.

        Rising steel +X% → adjust upward to estimate current market level.
        Falling → discount.
        No signal → passthrough with confidence 0.5.
        """
        base_price = _as_decimal(unit_price, "0")
        signal = self.get_current_price_signal(material_family, db)
        if signal is None or signal.trend_direction is None:
            return AdjustedPrice(
                original_price=base_price,
                adjusted_price=base_price,
                adjustment_pct=0.0,
                adjustment_reason="no_trend_signal_available",
                trend_direction=None,
                confidence=0.5,
            )

        trend_pct = _as_decimal(signal.trend_pct_30d, "0")
        direction = (signal.trend_direction or "stable").lower()

        if direction == "rising":
            factor = Decimal("1") + (trend_pct / Decimal("100"))
            reason = f"rising {signal.commodity_name} (+{trend_pct}% 30d) — upward adjust"
            confidence = 0.75
        elif direction == "falling":
            factor = Decimal("1") + (trend_pct / Decimal("100"))  # trend_pct negative
            reason = f"falling {signal.commodity_name} ({trend_pct}% 30d) — downward adjust"
            confidence = 0.70
        else:
            factor = Decimal("1")
            reason = f"stable {signal.commodity_name} — no adjustment"
            confidence = 0.80

        adjusted = (base_price * factor).quantize(Decimal("0.0001"))
        pct_delta = float(factor - Decimal("1")) * 100.0

        return AdjustedPrice(
            original_price=base_price,
            adjusted_price=adjusted,
            adjustment_pct=round(pct_delta, 4),
            adjustment_reason=reason,
            trend_direction=direction,
            confidence=confidence,
        )

    def ingest_commodity_signal(
        self,
        signal_data: dict[str, Any],
        db: Session,
    ) -> CommodityPriceSignal:
        """
        Insert a new commodity signal. Auto-detect trend direction / valley
        by scanning the last 90 days of same-commodity / material_family
        signals.
        """
        commodity = str(signal_data.get("commodity_name") or "").strip()
        if not commodity:
            raise ValueError("signal_data.commodity_name is required")

        material_family = signal_data.get("material_family_tag")
        price = _as_decimal(signal_data.get("price_per_unit"), "0")
        if price <= 0:
            raise ValueError("price_per_unit must be positive")

        price_date = signal_data.get("price_date") or _today()
        if isinstance(price_date, str):
            price_date = datetime.fromisoformat(price_date).date()

        window_start = price_date - timedelta(days=90)
        window_signals = (
            db.query(CommodityPriceSignal)
            .filter(
                CommodityPriceSignal.commodity_name == commodity,
                CommodityPriceSignal.price_date >= window_start,
                CommodityPriceSignal.price_date < price_date,
            )
            .order_by(CommodityPriceSignal.price_date.asc())
            .all()
        )

        # Trend over ~30 days (compare to the most recent signal ≤30d old)
        trend_direction = signal_data.get("trend_direction")
        trend_pct_30d = signal_data.get("trend_pct_30d")
        if trend_direction is None or trend_pct_30d is None:
            trend_direction, trend_pct_30d = self._infer_trend(price, price_date, window_signals)

        # Valley detection: price is the min over the 90-day window
        is_valley = signal_data.get("is_valley")
        if is_valley is None:
            if not window_signals:
                is_valley = False
            else:
                prior_min = min(_as_decimal(s.price_per_unit) for s in window_signals)
                is_valley = price <= prior_min

        record = CommodityPriceSignal(
            commodity_name=commodity,
            material_family_tag=material_family,
            price_per_unit=price,
            unit=str(signal_data.get("unit") or "kg")[:20],
            currency=str(signal_data.get("currency") or "USD")[:3].upper(),
            price_date=price_date,
            source=str(signal_data.get("source") or "manual")[:80],
            trend_direction=trend_direction,
            trend_pct_30d=_as_decimal(trend_pct_30d) if trend_pct_30d is not None else None,
            is_valley=bool(is_valley),
        )
        db.add(record)
        db.flush()
        logger.info(
            "ingest_commodity_signal commodity=%s family=%s date=%s price=%s trend=%s valley=%s",
            commodity, material_family, price_date, price, trend_direction, is_valley,
        )
        return record

    def _infer_trend(
        self,
        current_price: Decimal,
        current_date: date,
        history: list[CommodityPriceSignal],
    ) -> tuple[str, Decimal]:
        """Return (direction, trend_pct_30d)."""
        if not history:
            return "stable", Decimal("0")

        # Prefer a signal closest to 30 days ago, else earliest available.
        target = current_date - timedelta(days=30)
        candidates_ge = [s for s in history if s.price_date <= target]
        if candidates_ge:
            ref = candidates_ge[-1]  # history is asc → most recent ≤ target
        else:
            ref = history[0]

        ref_price = _as_decimal(ref.price_per_unit)
        if ref_price <= 0:
            return "stable", Decimal("0")

        delta_pct = ((current_price - ref_price) / ref_price) * Decimal("100")
        delta_pct = delta_pct.quantize(Decimal("0.0001"))

        if delta_pct > Decimal("1.0"):
            direction = "rising"
        elif delta_pct < Decimal("-1.0"):
            direction = "falling"
        else:
            direction = "stable"
        return direction, delta_pct


commodity_price_service = CommodityPriceService()
