"""
Phase 2A Batch 2 connector abstraction for part resolution, offers, and
availability.

Phase 2B Batch 1E adds an optional resilient wrapper around connector calls:
- timing
- telemetry
- rate limiting
- circuit breaker
- retry
- stale fallback

This intentionally still does not hardcode any real provider yet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from typing import Any

from app.integrations.connector_wrapper import ConnectorCallGuard, connector_call_guard
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


def _payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"repr": repr(value)}


class ResilientProductDataConnector(ProductDataConnector):
    """
    Wrap an existing ProductDataConnector with Batch 1E connector controls.

    Active SKU / BOM-line reads default to `priority="active"`.
    Callers can provide a lower `background_priority` wrapper instance if needed.
    """
    def __init__(
        self,
        base: ProductDataConnector,
        *,
        guard: ConnectorCallGuard | None = None,
        priority: str = "active",
        max_requests_per_minute: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.base = base
        self.guard = guard or connector_call_guard
        self.priority = priority
        self.max_requests_per_minute = max_requests_per_minute
        self.max_retries = max_retries

    @property
    def provider_name(self) -> str:
        return self.base.provider_name

    def search_products(self, identity: PartIdentity) -> list[ProductSearchCandidate]:
        return self.guard.execute(
            connector_name=self.provider_name,
            operation="search_products",
            func=lambda: self.base.search_products(identity),
            cache_key_payload=_payload(identity),
            fallback_factory=lambda: [],
            priority=self.priority,
            max_requests_per_minute=self.max_requests_per_minute,
            max_retries=self.max_retries,
        )

    def fetch_offers(self, candidate: ProductSearchCandidate) -> list[OfferDTO]:
        return self.guard.execute(
            connector_name=self.provider_name,
            operation="fetch_offers",
            func=lambda: self.base.fetch_offers(candidate),
            cache_key_payload=_payload(candidate),
            fallback_factory=lambda: [],
            priority=self.priority,
            max_requests_per_minute=self.max_requests_per_minute,
            max_retries=self.max_retries,
        )

    def fetch_availability(self, candidate: ProductSearchCandidate) -> list[AvailabilityDTO]:
        return self.guard.execute(
            connector_name=self.provider_name,
            operation="fetch_availability",
            func=lambda: self.base.fetch_availability(candidate),
            cache_key_payload=_payload(candidate),
            fallback_factory=lambda: [],
            priority=self.priority,
            max_requests_per_minute=self.max_requests_per_minute,
            max_retries=self.max_retries,
        )


def ensure_resilient_product_connector(
    connector: ProductDataConnector,
    *,
    priority: str = "active",
) -> ProductDataConnector:
    if isinstance(connector, ResilientProductDataConnector):
        return connector
    if isinstance(connector, NullProductDataConnector):
        return connector
    return ResilientProductDataConnector(connector, priority=priority)