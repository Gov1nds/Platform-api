"""FedExClient — rate & track (Blueprint §29.5)."""
from __future__ import annotations
import httpx
from app.core.config import settings

class FedExClient:
    name = "fedex"
    BASE = "https://apis.fedex.com"
    def __init__(self): self.key = getattr(settings, "FEDEX_API_KEY", "")
    @property
    def configured(self): return bool(self.key)

    async def rate(self, origin: str, destination: str, weight_kg: float, dims=None) -> dict:
        """Get shipping rate. Returns dict with cost, service_level, transit_days_min/max."""
        # Production: call fedex API with self.key
        return {"cost": 0, "service_level": "standard", "currency": "USD",
                "transit_days_min": 3, "transit_days_max": 7}

    async def track(self, tracking_number: str) -> list[dict]:
        """Get milestone list for a tracking number."""
        return []
