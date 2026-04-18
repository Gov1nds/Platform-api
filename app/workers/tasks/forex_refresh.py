"""
Forex rate refresh task.

Fetches current exchange rates from OpenExchangeRates API and updates
FXRate records in the database.

References: Blueprint Section 24
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


async def refresh_forex_rates(ctx: dict) -> dict:
    """
    Refresh all active forex rates.

    1. Fetches all active currency pairs from FXRate table.
    2. Calls OpenExchangeRates API for current rates.
    3. Updates FXRate records with new rates.
    4. Marks old rates as STALE.
    """
    from app.core.database import SessionLocal
    from app.models.market import FXRate

    db = SessionLocal()
    updated = 0
    errors = 0
    try:
        rates = db.query(FXRate).all()
        now = datetime.now(timezone.utc)

        # Attempt live fetch
        live_rates = await _fetch_live_rates()

        for rate in rates:
            try:
                pair_key = f"{rate.base_currency}_{rate.quote_currency}"
                if live_rates and pair_key in live_rates:
                    rate.rate = live_rates[pair_key]
                    rate.freshness_status = "FRESH"
                    rate.source = "openexchangerates"
                else:
                    # Check TTL
                    if rate.fetched_at and (now - rate.fetched_at).total_seconds() > rate.ttl_seconds:
                        rate.freshness_status = "STALE"
                    else:
                        rate.freshness_status = "FRESH"
                rate.fetched_at = now
                rate.last_verified_at = now
                updated += 1
            except Exception:
                errors += 1
                logger.debug("Failed to update rate %s/%s", rate.base_currency, rate.quote_currency)

        db.commit()
        logger.info("Forex refresh: %d updated, %d errors", updated, errors)
    except Exception:
        logger.exception("Forex refresh failed")
        db.rollback()
        errors += 1
    finally:
        db.close()

    return {"updated": updated, "errors": errors}


async def _fetch_live_rates() -> dict[str, float]:
    """Fetch live rates from OpenExchangeRates API."""
    from app.core.config import settings
    app_id = getattr(settings, "OPEN_EXCHANGE_RATES_APP_ID", "")
    if not app_id:
        return {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://openexchangerates.org/api/latest.json?app_id={app_id}"
            )
            if resp.status_code == 200:
                data = resp.json()
                usd_rates = data.get("rates", {})
                result = {}
                for currency, rate in usd_rates.items():
                    result[f"USD_{currency}"] = rate
                return result
    except Exception:
        logger.debug("OpenExchangeRates fetch failed", exc_info=True)
    return {}
