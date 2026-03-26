"""Misumi API client — FIXED: simulated results marked."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("misumi_client")


class MisumiClient(SupplierAPIClient):
    BASE_URL = "https://api.misumi-ec.com"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key=api_key, name="Misumi")

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return [{
                "mpn": query, "manufacturer": "Simulated", "description": query,
                "price": 0.30, "stock": 2000, "lead_days": 7, "supplier": "Misumi (sim)",
                "is_simulated": True,
            }]
        return []

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None