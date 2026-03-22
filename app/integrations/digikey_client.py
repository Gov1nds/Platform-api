"""DigiKey API client."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("digikey_client")


class DigiKeyClient(SupplierAPIClient):
    def __init__(self, client_id: str = "", client_secret: str = ""):
        super().__init__(api_key=client_id, name="DigiKey")
        self.client_secret = client_secret

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return self._simulated(query, quantity)
        # Real API integration would go here (OAuth2 flow)
        return self._simulated(query, quantity)

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None

    def _simulated(self, query: str, qty: int) -> List[Dict]:
        return [{
            "mpn": query, "manufacturer": "Simulated", "description": query,
            "price": 0.75, "stock": 5000, "lead_days": 10, "supplier": "DigiKey (sim)",
        }]
