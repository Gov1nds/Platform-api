"""
Phase 2B Batch 1B catalog discovery connector abstraction.

This intentionally does not implement real providers yet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.canonical_catalog import CatalogSearchCandidate
from app.schemas.enrichment import PartIdentity


class CatalogSearchConnector(ABC):
    provider_name: str = "abstract"

    @abstractmethod
    def search_parts(self, part_identity: PartIdentity) -> list[CatalogSearchCandidate]:
        """
        Resolve a normalized part identity into catalog SKU candidates.
        """
        raise NotImplementedError


class NullCatalogSearchConnector(CatalogSearchConnector):
    """
    Default no-op connector used until a real provider is wired in.
    """
    provider_name = "null_catalog"

    def search_parts(self, part_identity: PartIdentity) -> list[CatalogSearchCandidate]:
        return []