"""DigiKey API client — FIXED: simulated results marked as is_simulated."""
import logging
from typing import Dict, Any, Optional, List
from app.integrations.base_client import SupplierAPIClient

logger = logging.getLogger("digikey_client")


class DigiKeyClient(SupplierAPIClient):
    BASE_URL = "https://api.digikey.com/v3"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        super().__init__(api_key=client_id, name="DigiKey")
        self.client_secret = client_secret

    def is_configured(self) -> bool:
        return bool(self.api_key and self.client_secret)

    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return [{
                "mpn": query, "manufacturer": "Simulated", "description": query,
                "price": 0.45, "stock": 500, "lead_days": 10, "supplier": "DigiKey (sim)",
                "is_simulated": True,
            }]
        # Real API call would go here
        return []

    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        results = self.search_part(mpn, quantity)
        return results[0] if results else None