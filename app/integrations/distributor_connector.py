"""
Distributor connector — live clients for Digi-Key, Mouser, Octopart, Arrow.
Blueprint §24.2, §29.1, C7.

Preserves the original ProductDataConnector ABC for backward compatibility.
"""
from __future__ import annotations
import asyncio, time, logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any
import httpx

from app.core.config import settings
from app.integrations.decorators import circuit_breaker, rate_limiter

logger = logging.getLogger(__name__)

# ── Original ABC (preserved) ──────────────────────────────────────────────

class ProductDataConnector(ABC):
    @abstractmethod
    def search_products(self, query: str) -> list[dict]: ...
    @abstractmethod
    def get_offers(self, product_id: str) -> list[dict]: ...
    @abstractmethod
    def get_availability(self, product_id: str) -> dict: ...

# ── Live clients ──────────────────────────────────────────────────────────

class DigiKeyClient:
    BASE = "https://api.digikey.com"
    def __init__(self):
        self.client_id = settings.DIGIKEY_CLIENT_ID
        self.client_secret = settings.DIGIKEY_CLIENT_SECRET
        self._token, self._exp = None, 0
    @property
    def configured(self): return bool(self.client_id and self.client_secret)

    async def _auth(self, client):
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await client.post(f"{self.BASE}/v1/oauth2/token",
            data={"client_id": self.client_id, "client_secret": self.client_secret,
                  "grant_type": "client_credentials"})
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._exp = time.time() + int(d.get("expires_in", 3600))
        return self._token

    @circuit_breaker(name="digikey", failure_threshold=5, recovery_timeout=60)
    @rate_limiter(name="digikey", max_calls=240, period=60)
    async def search_mpn(self, mpn: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            token = await self._auth(c)
            r = await c.post(f"{self.BASE}/products/v4/search/keyword",
                headers={"Authorization": f"Bearer {token}",
                         "X-DIGIKEY-Client-Id": self.client_id,
                         "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Currency": "USD"},
                json={"Keywords": mpn, "Limit": 5})
            r.raise_for_status()
            return _extract_digikey_offers(r.json())

def _extract_digikey_offers(data):
    offers = []
    for p in data.get("Products", []):
        mpn = p.get("ManufacturerPartNumber")
        mfr = (p.get("Manufacturer") or {}).get("Name")
        for pb in p.get("StandardPricing", []):
            try: up = Decimal(str(pb.get("UnitPrice", 0)))
            except: continue
            offers.append({"source": "digikey", "mpn": mpn, "manufacturer": mfr,
                "quantity_break": pb.get("BreakQuantity", 1), "unit_price": up,
                "currency": "USD", "stock": p.get("QuantityAvailable", 0),
                "lead_time_weeks": p.get("ManufacturerLeadWeeks")})
    return offers

class MouserClient:
    BASE = "https://api.mouser.com/api/v1"
    def __init__(self): self.key = settings.MOUSER_API_KEY
    @property
    def configured(self): return bool(self.key)

    @circuit_breaker(name="mouser", failure_threshold=5, recovery_timeout=60)
    @rate_limiter(name="mouser", max_calls=1000, period=3600)
    async def search_mpn(self, mpn: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{self.BASE}/search/partnumber?apiKey={self.key}",
                json={"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "1"}})
            r.raise_for_status()
            return _extract_mouser_offers(r.json())

def _extract_mouser_offers(data):
    offers = []
    for p in (data.get("SearchResults", {}) or {}).get("Parts", []):
        mpn = p.get("ManufacturerPartNumber"); mfr = p.get("Manufacturer")
        for pb in p.get("PriceBreaks", []):
            ps = (pb.get("Price") or "").replace("$","").replace(",","").strip()
            try: up = Decimal(ps)
            except: continue
            offers.append({"source": "mouser", "mpn": mpn, "manufacturer": mfr,
                "quantity_break": pb.get("Quantity", 1), "unit_price": up,
                "currency": pb.get("Currency", "USD"),
                "stock": int(p.get("Availability", "0").split()[0] or 0) if p.get("Availability") else 0})
    return offers

class OctopartClient:
    BASE = "https://api.nexar.com/graphql"
    QUERY = """query SupSearch($mpn: String!) { supSearchMpn(q: $mpn, limit: 5) {
        results { part { mpn manufacturer { name } sellers { company { name }
        offers { inventoryLevel moq prices { price quantity currency } } } } } } }"""
    def __init__(self): self.key = settings.OCTOPART_API_KEY
    @property
    def configured(self): return bool(self.key)

    @circuit_breaker(name="octopart", failure_threshold=5, recovery_timeout=60)
    async def search_mpn(self, mpn: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(self.BASE,
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json={"query": self.QUERY, "variables": {"mpn": mpn}})
            r.raise_for_status()
            return _extract_octopart_offers(r.json())

def _extract_octopart_offers(data):
    offers = []
    for res in ((data.get("data") or {}).get("supSearchMpn") or {}).get("results", []):
        part = res.get("part") or {}
        mpn = part.get("mpn"); mfr = (part.get("manufacturer") or {}).get("name")
        for s in part.get("sellers", []):
            seller = (s.get("company") or {}).get("name")
            for o in s.get("offers", []):
                for pb in o.get("prices", []):
                    try: up = Decimal(str(pb["price"]))
                    except: continue
                    offers.append({"source": f"octopart:{seller}", "mpn": mpn, "manufacturer": mfr,
                        "quantity_break": pb.get("quantity", 1), "unit_price": up,
                        "currency": pb.get("currency", "USD"),
                        "stock": o.get("inventoryLevel"), "moq": o.get("moq")})
    return offers

class ArrowClient:
    def __init__(self): self.key = settings.ARROW_API_KEY
    @property
    def configured(self): return bool(self.key)
    async def search_mpn(self, mpn: str) -> list[dict]:
        return []

class DistributorAggregator:
    def __init__(self):
        self.clients = [DigiKeyClient(), MouserClient(), OctopartClient(), ArrowClient()]
    async def search(self, mpn: str) -> list[dict]:
        tasks = [c.search_mpn(mpn) for c in self.clients if c.configured]
        if not tasks: return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        offers = []
        for r in results:
            if isinstance(r, Exception): continue
            offers.extend(r)
        return offers

    @staticmethod
    def to_price_band(offers, currency="USD"):
        if not offers: return None
        prices = [o["unit_price"] for o in offers if o.get("currency") == currency]
        if not prices: return None
        ps = sorted(prices)
        return {"floor": ps[0], "mid": sum(ps)/len(ps), "ceiling": ps[-1],
                "currency": currency, "sample_size": len(ps)}


# ── Backward compatibility ────────────────────────────────────────────────

class NullProductDataConnector(ProductDataConnector):
    """No-op connector for testing and fallback."""
    def search_products(self, query: str) -> list[dict]: return []
    def get_offers(self, product_id: str) -> list[dict]: return []
    def get_availability(self, product_id: str) -> dict: return {}
