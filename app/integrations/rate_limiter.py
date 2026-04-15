"""
Phase 2B Batch 1E connector rate limiting.

Provides:
- per-connector per-minute request limits
- near-limit throttling
- in-memory fallback
- Redis support when REDIS_URL is configured

This module is additive and does not change prior batch behavior.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import redis

from app.core.config import settings


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    throttled: bool
    retry_after_ms: int
    used: int
    limit: int
    reason: str


class _InMemoryMinuteStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[int, int]] = {}

    def incr(self, key: str, now_ts: float) -> int:
        current_minute = int(now_ts // 60)
        with self._lock:
            used, bucket_minute = self._data.get(key, (0, current_minute))
            if bucket_minute != current_minute:
                used = 0
                bucket_minute = current_minute
            used += 1
            self._data[key] = (used, bucket_minute)
            return used

    def get(self, key: str, now_ts: float) -> int:
        current_minute = int(now_ts // 60)
        with self._lock:
            used, bucket_minute = self._data.get(key, (0, current_minute))
            if bucket_minute != current_minute:
                return 0
            return used


class ConnectorRateLimiter:
    def __init__(self) -> None:
        self._memory = _InMemoryMinuteStore()
        self._redis = None
        if settings.REDIS_URL:
            try:
                self._redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    def _bucket_key(self, connector_name: str, now_ts: float) -> str:
        minute = int(now_ts // 60)
        return f"connector_rate:{connector_name}:{minute}"

    def _incr(self, connector_name: str, now_ts: float) -> int:
        key = self._bucket_key(connector_name, now_ts)
        if self._redis is not None:
            pipe = self._redis.pipeline()
            pipe.incr(key, 1)
            pipe.expire(key, 70)
            used, _ = pipe.execute()
            return int(used)
        return self._memory.incr(key, now_ts)

    def _retry_after_ms(self, now_ts: float) -> int:
        next_minute = (math.floor(now_ts / 60) + 1) * 60
        return max(0, int((next_minute - now_ts) * 1000))

    def acquire(
        self,
        *,
        connector_name: str,
        max_requests_per_minute: int,
        priority: str = "active",
        now_ts: float | None = None,
    ) -> RateLimitDecision:
        now_ts = now_ts if now_ts is not None else _now_ts()
        used = self._incr(connector_name, now_ts)
        near_limit = used >= max(1, math.floor(max_requests_per_minute * 0.8))
        exceeded = used > max_requests_per_minute

        if exceeded:
            return RateLimitDecision(
                allowed=False,
                throttled=priority == "active",
                retry_after_ms=self._retry_after_ms(now_ts),
                used=used,
                limit=max_requests_per_minute,
                reason="exceeded",
            )

        if near_limit:
            return RateLimitDecision(
                allowed=True,
                throttled=True,
                retry_after_ms=min(750, self._retry_after_ms(now_ts)),
                used=used,
                limit=max_requests_per_minute,
                reason="near_limit",
            )

        return RateLimitDecision(
            allowed=True,
            throttled=False,
            retry_after_ms=0,
            used=used,
            limit=max_requests_per_minute,
            reason="ok",
        )


connector_rate_limiter = ConnectorRateLimiter()