"""Open Exchange Rates + XE.com clients (Blueprint §24.2, §29.2)."""
from __future__ import annotations
import httpx
from app.core.config import settings
from app.integrations.decorators import circuit_breaker

class OpenExchangeRatesClient:
    BASE = "https://openexchangerates.org/api"
    def __init__(self):
        self.app_id = settings.OPEN_EXCHANGE_RATES_APP_ID
    @property
    def configured(self): return bool(self.app_id)

    @circuit_breaker(name="oxr", failure_threshold=3, recovery_timeout=120)
    async def latest(self, base: str = "USD") -> dict:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.BASE}/latest.json",
                            params={"app_id": self.app_id, "base": base})
            r.raise_for_status()
            return r.json()

    async def historical(self, date_iso: str, base: str = "USD") -> dict:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.BASE}/historical/{date_iso}.json",
                            params={"app_id": self.app_id, "base": base})
            r.raise_for_status()
            return r.json()

class XEClient:
    BASE = "https://xecdapi.xe.com/v1"
    def __init__(self): self.key = settings.XE_API_KEY
    @property
    def configured(self): return bool(self.key)
    async def latest(self, base="USD"): return {"base": base, "rates": {}}
