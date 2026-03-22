"""Mouser API client."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("mouser_client")


class MouserClient(SupplierAPIClient):
    BASE_URL = "https://api.mouser.com/api/v1"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key=api_key, name="Mouser")

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        cached = self._get_cached(f"search:{query}")
        if cached:
            return cached

        if not self.is_configured():
            return self._simulated_search(query, quantity)

        try:
            import urllib.request
            import json
            url = f"{self.BASE_URL}/search/partnumber?apiKey={self.api_key}"
            data = json.dumps({"SearchByPartRequest": {"mouserPartNumber": query}}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                parts = result.get("SearchResults", {}).get("Parts", [])
                out = [
                    {
                        "mpn": p.get("ManufacturerPartNumber", ""),
                        "manufacturer": p.get("Manufacturer", ""),
                        "description": p.get("Description", ""),
                        "price": self._extract_price(p, quantity),
                        "stock": int(p.get("Availability", "0").replace(",", "").split()[0] or 0),
                        "lead_days": 14,
                        "supplier": "Mouser",
                        "url": p.get("ProductDetailUrl", ""),
                    }
                    for p in parts[:5]
                ]
                self._set_cached(f"search:{query}", out)
                return out
        except Exception as e:
            logger.warning(f"Mouser API error: {e}")
            return self._simulated_search(query, quantity)

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None

    def _extract_price(self, part: dict, qty: int) -> float:
        for pb in part.get("PriceBreaks", []):
            try:
                min_qty = int(pb.get("Quantity", "1").replace(",", ""))
                if qty >= min_qty:
                    return float(pb.get("Price", "0").replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                continue
        return 0.0

    def _simulated_search(self, query: str, qty: int) -> List[Dict]:
        return [{
            "mpn": query, "manufacturer": "Simulated", "description": query,
            "price": 0.50, "stock": 1000, "lead_days": 14, "supplier": "Mouser (sim)",
        }]
