"""
Phase 2B Batch 1E connector telemetry aggregation.

Aggregates connector call health per connector_name and minute window into
connector_health_metrics. This is additive and does not change prior batch
behavior.

Tracked:
- request_count
- error_count
- rate_limit_429_count
- average_latency_ms
- timeout_count
- retry_count
- throttle_count

Notes:
- Uses app.models.canonical.ConnectorHealthMetrics when available.
- Falls back to no-op persistence if the Phase 2B Batch 1A model module is not
  present yet in the checked-out repo state.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

try:
    from app.models.canonical import ConnectorHealthMetrics
except Exception:  # pragma: no cover - defensive for partially-applied repos
    ConnectorHealthMetrics = None  # type: ignore[assignment]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _minute_window(ts: datetime) -> tuple[datetime, datetime]:
    ts = ts.astimezone(timezone.utc)
    start = ts.replace(second=0, microsecond=0)
    end = start + timedelta(minutes=1)
    return start, end


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


@dataclass(slots=True)
class ConnectorTelemetryEvent:
    connector_name: str
    operation: str
    status: str
    latency_ms: int
    error_class: str | None = None
    retry_count: int = 0
    throttled: bool = False
    state: str = "CLOSED"
    timestamp: datetime | None = None


class ConnectorTelemetryService:
    def _get_or_create_row(
        self,
        db: Session,
        *,
        connector_name: str,
        operation: str,
        timestamp: datetime,
    ):
        if ConnectorHealthMetrics is None:
            return None

        window_started_at, window_ended_at = _minute_window(timestamp)
        row = (
            db.query(ConnectorHealthMetrics)
            .filter(
                ConnectorHealthMetrics.connector_name == connector_name,
                ConnectorHealthMetrics.metric_scope == operation,
                ConnectorHealthMetrics.window_started_at == window_started_at,
                ConnectorHealthMetrics.window_ended_at == window_ended_at,
            )
            .first()
        )
        if row is not None:
            return row

        row = ConnectorHealthMetrics(
            connector_name=connector_name,
            metric_scope=operation,
            status="UNKNOWN",
            success_count=0,
            error_count=0,
            timeout_count=0,
            throttle_count=0,
            retry_count=0,
            latency_p50_ms=None,
            latency_p95_ms=None,
            freshness_lag_seconds=None,
            last_success_at=None,
            last_error_at=None,
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            source_metadata={},
        )
        db.add(row)
        db.flush()
        return row

    def _apply_event_to_row(self, row, event: ConnectorTelemetryEvent) -> None:
        if row is None:
            return

        timestamp = event.timestamp or _now()
        metadata = dict(row.source_metadata or {})

        request_count = _safe_int(metadata.get("request_count"), 0) + 1
        error_count = _safe_int(metadata.get("error_count"), 0)
        rate_limit_429_count = _safe_int(metadata.get("rate_limit_429_count"), 0)
        total_latency_ms = _safe_int(metadata.get("total_latency_ms"), 0) + max(0, event.latency_ms)

        if event.status != "success":
            error_count += 1
        if event.error_class == "429":
            rate_limit_429_count += 1

        metadata.update(
            {
                "request_count": request_count,
                "error_count": error_count,
                "rate_limit_429_count": rate_limit_429_count,
                "total_latency_ms": total_latency_ms,
                "average_latency_ms": round(total_latency_ms / max(1, request_count), 2),
                "last_error_class": event.error_class,
                "circuit_state": event.state,
            }
        )

        row.source_metadata = metadata
        row.success_count = int(row.success_count or 0) + (1 if event.status == "success" else 0)
        row.error_count = int(row.error_count or 0) + (0 if event.status == "success" else 1)
        row.retry_count = int(row.retry_count or 0) + max(0, event.retry_count)
        row.throttle_count = int(row.throttle_count or 0) + (1 if event.throttled else 0)
        row.timeout_count = int(row.timeout_count or 0) + (1 if event.error_class == "timeout" else 0)

        # The current schema does not include an explicit average latency column,
        # so keep the average in source_metadata and preserve rough latency hints
        # in the existing integer latency columns.
        avg_latency = int(round(metadata["average_latency_ms"]))
        row.latency_p50_ms = avg_latency
        row.latency_p95_ms = max(int(row.latency_p95_ms or 0), event.latency_ms)

        if event.status == "success":
            row.last_success_at = timestamp
            row.status = "OPEN" if event.state == "OPEN" else "HEALTHY"
        else:
            row.last_error_at = timestamp
            row.status = "OPEN" if event.state == "OPEN" else "DEGRADED"

        row.updated_at = timestamp

    def record(
        self,
        *,
        connector_name: str,
        operation: str,
        status: str,
        latency_ms: int,
        error_class: str | None = None,
        retry_count: int = 0,
        throttled: bool = False,
        state: str = "CLOSED",
        timestamp: datetime | None = None,
        db: Session | None = None,
    ) -> None:
        if ConnectorHealthMetrics is None:
            return

        owns_session = db is None
        session = db or SessionLocal()
        try:
            ts = timestamp or _now()
            event = ConnectorTelemetryEvent(
                connector_name=connector_name,
                operation=operation,
                status=status,
                latency_ms=latency_ms,
                error_class=error_class,
                retry_count=retry_count,
                throttled=throttled,
                state=state,
                timestamp=ts,
            )
            row = self._get_or_create_row(
                session,
                connector_name=connector_name,
                operation=operation,
                timestamp=ts,
            )
            self._apply_event_to_row(row, event)
            if owns_session:
                session.commit()
        except Exception:
            if owns_session:
                session.rollback()
            logger.debug("Failed to persist connector telemetry", exc_info=True)
        finally:
            if owns_session:
                session.close()


connector_telemetry_service = ConnectorTelemetryService()