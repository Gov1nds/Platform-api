from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.circuit_breaker import ConnectorCircuitBreaker, CircuitOpenError
from app.integrations.connector_wrapper import ConnectorCallGuard
from app.integrations.rate_limiter import ConnectorRateLimiter


class FakeTelemetry:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, **kwargs) -> None:
        self.events.append(kwargs)


class MutableClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_rate_limiting_blocks_after_limit_and_uses_fallback():
    limiter = ConnectorRateLimiter()
    telemetry = FakeTelemetry()
    breaker = ConnectorCircuitBreaker()
    clock = MutableClock(start=0.0)

    guard = ConnectorCallGuard(
        telemetry=telemetry,
        rate_limiter=limiter,
        circuit_breaker=breaker,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.now,
    )

    calls = {"count": 0}

    def _ok():
        calls["count"] += 1
        return {"ok": True}

    result1 = guard.execute(
        connector_name="mouser",
        operation="search_products",
        func=_ok,
        cache_key_payload={"q": "abc"},
        fallback_factory=lambda: {"fallback": True},
        max_requests_per_minute=2,
        max_retries=0,
    )
    result2 = guard.execute(
        connector_name="mouser",
        operation="search_products",
        func=_ok,
        cache_key_payload={"q": "def"},
        fallback_factory=lambda: {"fallback": True},
        max_requests_per_minute=2,
        max_retries=0,
    )
    result3 = guard.execute(
        connector_name="mouser",
        operation="search_products",
        func=_ok,
        cache_key_payload={"q": "ghi"},
        fallback_factory=lambda: {"fallback": True},
        max_requests_per_minute=2,
        max_retries=0,
    )

    assert result1 == {"ok": True}
    assert result2 == {"ok": True}
    assert result3["_stale"] is True
    assert result3["fallback"] is True
    assert calls["count"] == 2
    assert any(event.get("error_class") == "429" for event in telemetry.events)


def test_circuit_breaker_opens_then_recovers_half_open_to_closed():
    limiter = ConnectorRateLimiter()
    telemetry = FakeTelemetry()
    breaker = ConnectorCircuitBreaker()
    clock = MutableClock(start=1000.0)

    guard = ConnectorCallGuard(
        telemetry=telemetry,
        rate_limiter=limiter,
        circuit_breaker=breaker,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.now,
    )

    def _always_fails():
        raise TimeoutError("timeout")

    for i in range(5):
        result = guard.execute(
            connector_name="digikey",
            operation="fetch_offers",
            func=_always_fails,
            cache_key_payload={"attempt": i},
            fallback_factory=lambda: [],
            max_requests_per_minute=100,
            circuit_failure_threshold=0.10,
            circuit_min_samples=5,
            circuit_cooldown_seconds=10,
            max_retries=0,
        )
        assert result == []

    with_telemetry_count = len(telemetry.events)

    blocked = guard.execute(
        connector_name="digikey",
        operation="fetch_offers",
        func=lambda: [{"should_not_run": True}],
        cache_key_payload={"attempt": "blocked"},
        fallback_factory=lambda: [],
        max_requests_per_minute=100,
        circuit_failure_threshold=0.10,
        circuit_min_samples=5,
        circuit_cooldown_seconds=10,
        max_retries=0,
    )
    assert blocked == []
    assert len(telemetry.events) == with_telemetry_count + 1
    assert telemetry.events[-1]["error_class"] == "circuit_open"

    clock.sleep(11.0)

    recovered = guard.execute(
        connector_name="digikey",
        operation="fetch_offers",
        func=lambda: [{"ok": True}],
        cache_key_payload={"attempt": "recovery"},
        fallback_factory=lambda: [],
        max_requests_per_minute=100,
        circuit_failure_threshold=0.10,
        circuit_min_samples=5,
        circuit_cooldown_seconds=10,
        max_retries=0,
    )
    assert recovered == [{"ok": True}]
    state = breaker.state(connector_name="digikey", now_ts=clock.now())
    assert state.state == "CLOSED"


def test_retry_logic_retries_transient_timeout_then_succeeds():
    limiter = ConnectorRateLimiter()
    telemetry = FakeTelemetry()
    breaker = ConnectorCircuitBreaker()
    clock = MutableClock(start=2000.0)

    guard = ConnectorCallGuard(
        telemetry=telemetry,
        rate_limiter=limiter,
        circuit_breaker=breaker,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.now,
    )

    attempts = {"count": 0}

    def _flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("temporary timeout")
        return {"ok": True}

    result = guard.execute(
        connector_name="arrow",
        operation="fetch_availability",
        func=_flaky,
        cache_key_payload={"sku": "A-1"},
        fallback_factory=lambda: {"fallback": True},
        max_requests_per_minute=100,
        max_retries=2,
        retry_base_delay_ms=100,
    )

    assert result == {"ok": True}
    assert attempts["count"] == 3
    failure_events = [event for event in telemetry.events if event["status"] == "failed"]
    success_events = [event for event in telemetry.events if event["status"] == "success"]
    assert len(failure_events) == 2
    assert len(success_events) == 1
    assert success_events[0]["retry_count"] == 2