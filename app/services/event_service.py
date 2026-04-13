"""
Platform event system — domain events + legacy tracking.

Provides:
- DomainEvent dataclass with full EventEnvelope fields
- EventBroker abstraction (InMemory, Redis, pluggable Kafka)
- Typed emission functions for canonical domain events
- Legacy track() retained for backward compatibility

References: GAP-006, event-contract-review.md, architecture.md CC-06
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.events import PlatformEvent

logger = logging.getLogger(__name__)


# ── Domain Event Envelope ────────────────────────────────────────────────────

@dataclass
class DomainEvent:
    """Canonical event envelope per event-contract-review.md."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    correlation_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    producer: str = "platform-api"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict = field(default_factory=dict)
    schema_version: str = "2025-01"


# ── Broker Protocol ─────────────────────────────────────────────────────────

class EventBroker(Protocol):
    async def publish(self, topic: str, event: DomainEvent) -> None: ...


class InMemoryBroker:
    """Dev/test broker — stores events in memory."""

    def __init__(self) -> None:
        self.events: list[tuple[str, DomainEvent]] = []

    async def publish(self, topic: str, event: DomainEvent) -> None:
        self.events.append((topic, event))
        logger.debug("Event published [%s]: %s", topic, event.event_type)


class RedisBroker:
    """Staging broker — publishes to Redis pub/sub."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                import redis.asyncio as aioredis
                self._client = aioredis.from_url(self._url, decode_responses=True)
            except Exception:
                logger.warning("Redis broker unavailable — falling back to memory")
                return None
        return self._client

    async def publish(self, topic: str, event: DomainEvent) -> None:
        client = await self._get_client()
        if client:
            try:
                import json
                await client.publish(topic, json.dumps({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "timestamp": event.timestamp,
                    "trace_id": event.trace_id,
                    "schema_version": event.schema_version,
                }))
            except Exception:
                logger.warning("Failed to publish event to Redis", exc_info=True)


# ── Broker Singleton ────────────────────────────────────────────────────────

_broker: EventBroker | None = None


def get_broker() -> EventBroker:
    global _broker
    if _broker is None:
        from app.core.config import settings
        if settings.EVENT_BROKER_TYPE == "redis" and settings.REDIS_URL:
            _broker = RedisBroker(settings.REDIS_URL)
        else:
            _broker = InMemoryBroker()
    return _broker


# ── Typed Event Emitters ────────────────────────────────────────────────────

def emit_project_status_changed(
    db: Session,
    project_id: str,
    old_status: str,
    new_status: str,
    actor_id: str | None,
    organization_id: str | None = None,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="project.status_changed",
        trace_id=trace_id,
        payload={
            "project_id": project_id,
            "old_status": old_status,
            "new_status": new_status,
            "actor_id": actor_id,
            "organization_id": organization_id,
        },
    )
    _fire_and_forget(event, "project.lifecycle")
    return event


def emit_bom_line_status_changed(
    db: Session,
    bom_line_id: str,
    old_status: str,
    new_status: str,
    actor_id: str | None,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="bom_line.status_changed",
        trace_id=trace_id,
        payload={
            "bom_line_id": bom_line_id,
            "old_status": old_status,
            "new_status": new_status,
            "actor_id": actor_id,
        },
    )
    _fire_and_forget(event, "bom.lifecycle")
    return event


def emit_rfq_sent(
    db: Session,
    rfq_id: str,
    actor_id: str | None,
    vendor_count: int = 0,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="rfq.sent",
        trace_id=trace_id,
        payload={
            "rfq_id": rfq_id,
            "actor_id": actor_id,
            "vendor_count": vendor_count,
        },
    )
    _fire_and_forget(event, "sourcing.lifecycle")
    return event


def emit_quote_received(
    db: Session,
    quote_id: str,
    rfq_id: str,
    vendor_id: str,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="quote.received",
        trace_id=trace_id,
        payload={
            "quote_id": quote_id,
            "rfq_id": rfq_id,
            "vendor_id": vendor_id,
        },
    )
    _fire_and_forget(event, "sourcing.lifecycle")
    return event


def emit_po_created(
    db: Session,
    po_id: str,
    project_id: str,
    actor_id: str | None,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="po.created",
        trace_id=trace_id,
        payload={
            "po_id": po_id,
            "project_id": project_id,
            "actor_id": actor_id,
        },
    )
    _fire_and_forget(event, "order.lifecycle")
    return event


def emit_shipment_milestone(
    db: Session,
    shipment_id: str,
    milestone_type: str,
    status: str,
    trace_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_type="shipment.milestone",
        trace_id=trace_id,
        payload={
            "shipment_id": shipment_id,
            "milestone_type": milestone_type,
            "status": status,
        },
    )
    _fire_and_forget(event, "logistics.lifecycle")
    return event


def _fire_and_forget(event: DomainEvent, topic: str) -> None:
    """Best-effort async publish. Non-blocking."""
    try:
        import asyncio
        broker = get_broker()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broker.publish(topic, event))
        except RuntimeError:
            # No running loop — sync context
            asyncio.run(broker.publish(topic, event))
    except Exception:
        logger.debug("Event publish failed (non-fatal)", exc_info=True)


# ── Legacy track() — backward compatible ─────────────────────────────────────

def track(
    db: Session,
    event_type: str,
    actor_id: str | None = None,
    actor_type: str = "user",
    resource_type: str | None = None,
    resource_id: str | None = None,
    payload: dict | None = None,
) -> PlatformEvent:
    """Legacy event tracking. Writes PlatformEvent record."""
    ev = PlatformEvent(
        event_type=event_type,
        actor_id=actor_id,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload or {},
    )
    db.add(ev)
    db.flush()
    return ev
