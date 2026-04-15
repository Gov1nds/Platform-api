"""
Phase 2B Batch 1E resilient connector wrapper.

Wraps external connector calls with:
- timing
- telemetry aggregation
- rate limiting
- circuit breaker
- retry with exponential backoff
- stale fallback to last known data

This module is additive and does not change prior batch behavior.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import httpx

from app.integrations.circuit_breaker import (
    CircuitOpenError,
    ConnectorCircuitBreaker,
    connector_circuit_breaker,
)
from app.integrations.rate_limiter import (
    ConnectorRateLimiter,
    connector_rate_limiter,
)
from app.services.connector_telemetry_service import (
    ConnectorTelemetryService,
    connector_telemetry_service,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class ConnectorCallGuard:
    def __init__(
        self,
        *,
        telemetry: ConnectorTelemetryService | None = None,
        rate_limiter: ConnectorRateLimiter | None = None,
        circuit_breaker: ConnectorCircuitBreaker | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self.telemetry = telemetry or connector_telemetry_service
        self.rate_limiter = rate_limiter or connector_rate_limiter
        self.circuit_breaker = circuit_breaker or connector_circuit_breaker
        self.sleep_fn = sleep_fn or time.sleep
        self.monotonic_fn = monotonic_fn or time.monotonic
        self._last_known: dict[str, Any] = {}

    def _classify_error(self, exc: Exception) -> str:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:
                return "429"
            if 500 <= status <= 599:
                return "5xx"
            return "hard_failure"
        return "hard_failure"

    def _should_retry(self, error_class: str, attempt: int, max_retries: int) -> bool:
        if attempt >= max_retries:
            return False
        return error_class in {"timeout", "5xx", "429"}

    def _mark_stale(self, value: Any, *, reason: str) -> Any:
        cloned = deepcopy(value)

        def _apply(obj: Any) -> None:
            if isinstance(obj, list):
                for item in obj:
                    _apply(item)
                return
            if isinstance(obj, dict):
                obj["_stale"] = True
                obj["_stale_reason"] = reason
                return
            if hasattr(obj, "freshness_status"):
                try:
                    setattr(obj, "freshness_status", "STALE")
                except Exception:
                    pass
            metadata = getattr(obj, "source_metadata", None)
            if isinstance(metadata, dict):
                metadata["stale_due_to_connector_failure"] = True
                metadata["stale_reason"] = reason

        _apply(cloned)
        return cloned

    def execute(
        self,
        *,
        connector_name: str,
        operation: str,
        func: Callable[[], T],
        cache_key_payload: dict[str, Any],
        fallback_factory: Callable[[], T] | None = None,
        priority: str = "active",
        max_requests_per_minute: int = 60,
        circuit_failure_threshold: float = 0.10,
        circuit_min_samples: int = 5,
        circuit_window_seconds: int = 300,
        circuit_cooldown_seconds: int = 60,
        max_retries: int = 2,
        retry_base_delay_ms: int = 200,
    ) -> T:
        cache_key = _hash_key(
            {
                "connector_name": connector_name,
                "operation": operation,
                "payload": cache_key_payload,
            }
        )
        start = self.monotonic_fn()

        try:
            state = self.circuit_breaker.before_request(
                connector_name=connector_name,
                cooldown_seconds=circuit_cooldown_seconds,
                window_seconds=circuit_window_seconds,
            )
        except CircuitOpenError:
            self.telemetry.record(
                connector_name=connector_name,
                operation=operation,
                status="failed",
                latency_ms=0,
                error_class="circuit_open",
                retry_count=0,
                throttled=False,
                state="OPEN",
            )
            if fallback_factory is not None:
                return self._mark_stale(fallback_factory(), reason="circuit_open")
            if cache_key in self._last_known:
                return self._mark_stale(self._last_known[cache_key], reason="circuit_open")
            raise

        decision = self.rate_limiter.acquire(
            connector_name=connector_name,
            max_requests_per_minute=max_requests_per_minute,
            priority=priority,
        )
        if decision.throttled and decision.allowed and decision.retry_after_ms > 0:
            self.sleep_fn(decision.retry_after_ms / 1000.0)

        if not decision.allowed:
            self.telemetry.record(
                connector_name=connector_name,
                operation=operation,
                status="failed",
                latency_ms=0,
                error_class="429",
                retry_count=0,
                throttled=True,
                state=state.state,
            )
            if fallback_factory is not None:
                return self._mark_stale(fallback_factory(), reason="rate_limited")
            if cache_key in self._last_known:
                return self._mark_stale(self._last_known[cache_key], reason="rate_limited")
            raise RuntimeError(f"Connector rate limit exceeded for {connector_name}")

        last_exc: Exception | None = None
        retry_count = 0

        for attempt in range(0, max_retries + 1):
            attempt_start = self.monotonic_fn()
            try:
                result = func()
                elapsed_ms = int((self.monotonic_fn() - attempt_start) * 1000)
                current_state = self.circuit_breaker.record_success(
                    connector_name=connector_name,
                    window_seconds=circuit_window_seconds,
                )
                self.telemetry.record(
                    connector_name=connector_name,
                    operation=operation,
                    status="success",
                    latency_ms=elapsed_ms,
                    error_class=None,
                    retry_count=retry_count,
                    throttled=decision.throttled,
                    state=current_state.state,
                )
                self._last_known[cache_key] = deepcopy(result)
                return result
            except Exception as exc:
                last_exc = exc
                error_class = self._classify_error(exc)
                elapsed_ms = int((self.monotonic_fn() - attempt_start) * 1000)
                current_state = self.circuit_breaker.record_failure(
                    connector_name=connector_name,
                    threshold=circuit_failure_threshold,
                    min_samples=circuit_min_samples,
                    cooldown_seconds=circuit_cooldown_seconds,
                    window_seconds=circuit_window_seconds,
                )
                self.telemetry.record(
                    connector_name=connector_name,
                    operation=operation,
                    status="failed",
                    latency_ms=elapsed_ms,
                    error_class=error_class,
                    retry_count=retry_count,
                    throttled=decision.throttled or error_class == "429",
                    state=current_state.state,
                )

                if not self._should_retry(error_class, attempt, max_retries):
                    break

                retry_count += 1
                backoff_ms = retry_base_delay_ms * (2 ** (retry_count - 1))
                # Do not keep retrying persistent rate limiting beyond limit.
                if error_class == "429" and retry_count >= max_retries:
                    break
                self.sleep_fn(backoff_ms / 1000.0)

        total_ms = int((self.monotonic_fn() - start) * 1000)
        logger.warning(
            "Connector call failed: connector=%s operation=%s retry_count=%s latency_ms=%s error=%s",
            connector_name,
            operation,
            retry_count,
            total_ms,
            last_exc,
        )

        if fallback_factory is not None:
            return self._mark_stale(fallback_factory(), reason="connector_failure")
        if cache_key in self._last_known:
            return self._mark_stale(self._last_known[cache_key], reason="connector_failure")
        raise RuntimeError(f"Connector call failed for {connector_name}/{operation}: {last_exc}") from last_exc


connector_call_guard = ConnectorCallGuard()