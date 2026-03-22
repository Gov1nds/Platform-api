"""Base client for external supplier API integrations."""
import logging
import time
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

logger = logging.getLogger("integrations")


class SupplierAPIClient(ABC):
    """Base class for all supplier API clients."""

    def __init__(self, api_key: str = "", name: str = "base"):
        self.api_key = api_key
        self.name = name
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, float] = {}
        self.TTL_SECONDS = 3600  # 1 hour cache

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            if time.time() - self._cache_ttl.get(key, 0) < self.TTL_SECONDS:
                return self._cache[key]
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any):
        self._cache[key] = value
        self._cache_ttl[key] = time.time()

    @abstractmethod
    def search_part(self, query: str, quantity: int = 1) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_pricing(self, mpn: str, quantity: int = 1) -> Optional[Dict[str, Any]]:
        pass

    def is_configured(self) -> bool:
        return bool(self.api_key)
