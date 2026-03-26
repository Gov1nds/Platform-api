"""ABB API client — FIXED: simulated results marked."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("abb_client")


class ABBClient(SupplierAPIClient):
    def __init__(self):
        super().__init__(api_key="", name="ABB")

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        return [{
            "mpn": query, "manufacturer": "Simulated", "description": query,
            "price": 5.00, "stock": 100, "lead_days": 21, "supplier": "ABB (sim)",
            "is_simulated": True,
        }]

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None