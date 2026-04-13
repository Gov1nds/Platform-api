from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import redis
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.enums import FreshnessStatus
from app.integrations.open_exchange_rates import OpenExchangeRatesClient
from app.models.market import FXRate
from app.services.integration_logging import integration_run

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FXService:
    CACHE_KEY = "fx:pair:{base}:{quote}"
    DEFAULT_TTL_SECONDS = 3600

    def __init__(self) -> None:
        self.client = OpenExchangeRatesClient()
        self._redis = None

    def _redis_client(self):
        if self._redis is not None:
            return self._redis
        try:
            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    def _cache_get(self, base: str, quote: str) -> Decimal | None:
        redis_client = self._redis_client()
        if not redis_client:
            return None
        raw = redis_client.get(self.CACHE_KEY.format(base=base, quote=quote))
        return Decimal(raw) if raw else None

    def _cache_set(self, base: str, quote: str, rate: Decimal, ttl_seconds: int | None = None) -> None:
        redis_client = self._redis_client()
        if not redis_client:
            return
        redis_client.setex(
            self.CACHE_KEY.format(base=base, quote=quote),
            ttl_seconds or self.DEFAULT_TTL_SECONDS,
            str(rate),
        )

    def get_rate(self, db: Session, *, base_currency: str, quote_currency: str) -> Decimal:
        base = base_currency.upper()
        quote = quote_currency.upper()
        if base == quote:
            return Decimal("1")

        cached = self._cache_get(base, quote)
        if cached is not None:
            return cached

        direct = self._latest_direct(db, base, quote)
        if direct is not None:
            self._cache_set(base, quote, direct)
            return direct

        derived = self._derive_rate(db, base, quote)
        if derived is not None:
            self._cache_set(base, quote, derived)
            return derived

        raise LookupError(f"No FX rate available for {base}/{quote}")

    def _latest_direct(self, db: Session, base: str, quote: str) -> Decimal | None:
        row = db.execute(
            select(FXRate)
            .where(FXRate.base_currency == base, FXRate.quote_currency == quote)
            .order_by(FXRate.fetched_at.desc().nullslast(), FXRate.created_at.desc())
        ).scalars().first()
        return Decimal(str(row.rate)) if row else None

    def _derive_rate(self, db: Session, base: str, quote: str) -> Decimal | None:
        usd_to_quote = self._latest_direct(db, "USD", quote)
        usd_to_base = self._latest_direct(db, "USD", base)
        if usd_to_quote is not None and usd_to_base is not None and usd_to_base != 0:
            return usd_to_quote / usd_to_base
        return None

    def refresh_rates(self, db: Session, *, symbols: list[str] | None = None) -> dict:
        symbols = sorted(set((symbols or []) + ["USD"]))
        if not self.client.configured():
            logger.warning("Open Exchange Rates not configured; keeping baseline FX rows")
            return {"status": "fallback", "updated": 0}

        payload = {"symbols": symbols}
        with integration_run(db, integration_id="INT-003", provider="open_exchange_rates", operation="refresh_fx", payload=payload) as run:
            data = self.client.latest(symbols=symbols)
            fetched_at = datetime.fromtimestamp(int(data["timestamp"]), tz=timezone.utc)
            updated = 0
            for quote, rate in data.get("rates", {}).items():
                if quote.upper() == "USD":
                    continue
                row = db.execute(
                    select(FXRate).where(and_(FXRate.base_currency == "USD", FXRate.quote_currency == quote.upper()))
                ).scalar_one_or_none()
                if row is None:
                    row = FXRate(base_currency="USD", quote_currency=quote.upper())
                    db.add(row)
                row.rate = rate
                row.source = "open_exchange_rates"
                row.confidence = Decimal("0.98")
                row.freshness_status = FreshnessStatus.FRESH
                row.ttl_seconds = self.DEFAULT_TTL_SECONDS
                row.fetched_at = fetched_at
                row.effective_from = fetched_at
                row.effective_to = fetched_at + timedelta(seconds=self.DEFAULT_TTL_SECONDS)
                row.last_verified_at = _now()
                row.provider_id = "open_exchange_rates"
                row.data_source = "live_api"
                self._cache_set("USD", quote.upper(), Decimal(str(rate)), self.DEFAULT_TTL_SECONDS)
                updated += 1
            run["response_count"] = updated
            return {"status": "success", "updated": updated, "fetched_at": fetched_at.isoformat()}

    def mark_provider_failure(self, db: Session) -> None:
        stale_cutoff = _now() - timedelta(seconds=self.DEFAULT_TTL_SECONDS)
        rows = db.execute(
            select(FXRate).where(
                and_(
                    FXRate.provider_id == "open_exchange_rates",
                    or_(FXRate.fetched_at.is_(None), FXRate.fetched_at < stale_cutoff),
                )
            )
        ).scalars().all()
        for row in rows:
            row.freshness_status = FreshnessStatus.STALE


fx_service = FXService()
