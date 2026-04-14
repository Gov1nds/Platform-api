"""
Phase 2A Batch 2 connector abstraction for part resolution, offers, and
availability.

This intentionally does not hardcode any real provider yet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.enrichment import (
    AvailabilityDTO,
    OfferDTO,
    PartIdentity,
    ProductSearchCandidate,
)


class ProductDataConnector(ABC):
    provider_name: str = "abstract"

    @abstractmethod
    def search_products(self, identity: PartIdentity) -> list[ProductSearchCandidate]:
        """
        Resolve a normalized part identity into vendor SKU candidates.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_offers(self, candidate: ProductSearchCandidate) -> list[OfferDTO]:
        """
        Fetch normalized pricing / commercial offers for a resolved SKU candidate.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_availability(self, candidate: ProductSearchCandidate) -> list[AvailabilityDTO]:
        """
        Fetch normalized availability snapshots for a resolved SKU candidate.
        """
        raise NotImplementedError


class NullProductDataConnector(ProductDataConnector):
    """
    Default no-op connector used until a real provider is wired in.
    """
    provider_name = "null"

    def search_products(self, identity: PartIdentity) -> list[ProductSearchCandidate]:
        return []

    def fetch_offers(self, candidate: ProductSearchCandidate) -> list[OfferDTO]:
        return []

    def fetch_availability(self, candidate: ProductSearchCandidate) -> list[AvailabilityDTO]:
        return []