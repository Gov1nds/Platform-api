"""ABB API client — for industrial automation components."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("abb_client")


class ABBClient(SupplierAPIClient):
    def __init__(self, api_key: str = ""):
        super().__init__(api_key=api_key, name="ABB")

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        return self._simulated(query, quantity)

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None

    def _simulated(self, query: str, qty: int) -> List[Dict]:
        return [{
            "mpn": query, "manufacturer": "ABB", "description": query,
            "price": 25.00, "stock": 100, "lead_days": 21, "supplier": "ABB (sim)",
        }]
