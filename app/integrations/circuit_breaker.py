"""
Phase 2B Batch 1E connector circuit breaker.

Provides:
- CLOSED / OPEN / HALF_OPEN transitions
- rolling failure-rate monitoring
- cooldown handling
- in-memory fallback

This module is additive and does not change prior batch behavior.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass(slots=True)
class CircuitState:
    state: str
    failure_rate: float
    sample_count: int
    opened_until_ts: float | None = None


class CircuitOpenError(RuntimeError):
    pass


class ConnectorCircuitBreaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[tuple[float, bool]]] = {}
        self._states: dict[str, CircuitState] = {}
        self._half_open_in_flight: set[str] = set()

    def _trim(self, connector_name: str, *, now_ts: float, window_seconds: int) -> deque[tuple[float, bool]]:
        events = self._events.setdefault(connector_name, deque())
        cutoff = now_ts - window_seconds
        while events and events[0][0] < cutoff:
            events.popleft()
        return events

    def _compute_failure_rate(
        self,
        connector_name: str,
        *,
        now_ts: float,
        window_seconds: int,
    ) -> tuple[float, int]:
        events = self._trim(connector_name, now_ts=now_ts, window_seconds=window_seconds)
        sample_count = len(events)
        if sample_count == 0:
            return 0.0, 0
        failures = sum(1 for _, ok in events if not ok)
        return failures / sample_count, sample_count

    def state(
        self,
        *,
        connector_name: str,
        now_ts: float | None = None,
        window_seconds: int = 300,
    ) -> CircuitState:
        now_ts = now_ts if now_ts is not None else _now_ts()
        with self._lock:
            existing = self._states.get(connector_name)
            if existing is None:
                rate, samples = self._compute_failure_rate(
                    connector_name,
                    now_ts=now_ts,
                    window_seconds=window_seconds,
                )
                existing = CircuitState(state="CLOSED", failure_rate=rate, sample_count=samples)
                self._states[connector_name] = existing
            return existing

    def before_request(
        self,
        *,
        connector_name: str,
        cooldown_seconds: int = 60,
        now_ts: float | None = None,
        window_seconds: int = 300,
    ) -> CircuitState:
        now_ts = now_ts if now_ts is not None else _now_ts()
        with self._lock:
            current = self.state(
                connector_name=connector_name,
                now_ts=now_ts,
                window_seconds=window_seconds,
            )

            if current.state == "OPEN":
                if current.opened_until_ts is not None and now_ts >= current.opened_until_ts:
                    current.state = "HALF_OPEN"
                else:
                    raise CircuitOpenError(f"Circuit OPEN for connector '{connector_name}'")

            if current.state == "HALF_OPEN":
                if connector_name in self._half_open_in_flight:
                    raise CircuitOpenError(f"Circuit HALF_OPEN busy for connector '{connector_name}'")
                self._half_open_in_flight.add(connector_name)

            return current

    def record_success(
        self,
        *,
        connector_name: str,
        now_ts: float | None = None,
        window_seconds: int = 300,
    ) -> CircuitState:
        now_ts = now_ts if now_ts is not None else _now_ts()
        with self._lock:
            events = self._trim(connector_name, now_ts=now_ts, window_seconds=window_seconds)
            events.append((now_ts, True))
            current = self._states.get(connector_name) or CircuitState(state="CLOSED", failure_rate=0.0, sample_count=0)
            current.failure_rate, current.sample_count = self._compute_failure_rate(
                connector_name,
                now_ts=now_ts,
                window_seconds=window_seconds,
            )
            current.state = "CLOSED"
            current.opened_until_ts = None
            self._states[connector_name] = current
            self._half_open_in_flight.discard(connector_name)
            return current

    def record_failure(
        self,
        *,
        connector_name: str,
        threshold: float = 0.10,
        min_samples: int = 5,
        cooldown_seconds: int = 60,
        now_ts: float | None = None,
        window_seconds: int = 300,
    ) -> CircuitState:
        now_ts = now_ts if now_ts is not None else _now_ts()
        with self._lock:
            events = self._trim(connector_name, now_ts=now_ts, window_seconds=window_seconds)
            events.append((now_ts, False))
            rate, samples = self._compute_failure_rate(
                connector_name,
                now_ts=now_ts,
                window_seconds=window_seconds,
            )

            current = self._states.get(connector_name) or CircuitState(state="CLOSED", failure_rate=0.0, sample_count=0)
            current.failure_rate = rate
            current.sample_count = samples

            if current.state == "HALF_OPEN":
                current.state = "OPEN"
                current.opened_until_ts = now_ts + cooldown_seconds
            elif samples >= min_samples and rate > threshold:
                current.state = "OPEN"
                current.opened_until_ts = now_ts + cooldown_seconds
            else:
                current.state = "CLOSED"
                current.opened_until_ts = None

            self._states[connector_name] = current
            self._half_open_in_flight.discard(connector_name)
            return current


connector_circuit_breaker = ConnectorCircuitBreaker()