"""Misumi API client — for mechanical/industrial components."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("misumi_client")


class MisumiClient(SupplierAPIClient):
    def __init__(self, api_key: str = ""):
        super().__init__(api_key=api_key, name="Misumi")

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return self._simulated(query, quantity)
        return self._simulated(query, quantity)

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None

    def _simulated(self, query: str, qty: int) -> List[Dict]:
        return [{
            "mpn": query, "manufacturer": "Misumi", "description": query,
            "price": 1.20, "stock": 500, "lead_days": 7, "supplier": "Misumi (sim)",
        }]
