"""
Integration client package.

Provides base client with circuit breaker, retry, timeout, and logging
for all external service integrations.

Submodules: storage, distributor, commodity, forex, tariff, carrier,
            email, sms, push, geolocation.

References: GAP-031, architecture.md CC-12, integration-contract-review.md
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class IntegrationClient:
    """
    Base HTTP client for external integrations.

    Features:
    - Per-call timeout
    - Manual retry with exponential backoff
    - In-process circuit breaker (shared with analyzer_service pattern)
    - IntegrationRunLog persistence
    """

    def __init__(
        self,
        integration_id: str,
        provider: str,
        base_url: str = "",
        timeout: float = 5.0,
        max_retries: int = 3,
    ):
        self.integration_id = integration_id
        self.provider = provider
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._failure_count = 0
        self._circuit_open = False
        self._last_failure: float = 0.0
        self._recovery_timeout = 30.0

    @property
    def circuit_open(self) -> bool:
        if self._circuit_open:
            if time.monotonic() - self._last_failure >= self._recovery_timeout:
                self._circuit_open = False
                return False
            return True
        return False

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        if self.circuit_open:
            self._log_run(0, "circuit_open")
            raise RuntimeError(
                f"Circuit breaker OPEN for {self.integration_id}/{self.provider}"
            )

        url = f"{self.base_url}{path}" if self.base_url else path
        h = headers or {}
        if settings.INTERNAL_API_KEY:
            h["X-Internal-Key"] = settings.INTERNAL_API_KEY

        last_exc: Exception | None = None
        start = time.monotonic()

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(
                        method, url, json=json, params=params, headers=h,
                    )
                    resp.raise_for_status()
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    self._failure_count = 0
                    self._log_run(elapsed_ms, "success", record_count=None)
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    self._record_failure()
                    self._log_run(
                        int((time.monotonic() - start) * 1000),
                        "failed",
                        error=str(e),
                    )
                    raise
                last_exc = e
                self._record_failure()
            except Exception as e:
                last_exc = e
                self._record_failure()

            if attempt < self.max_retries:
                import asyncio
                await asyncio.sleep(min(1.0 * (2 ** (attempt - 1)), 10.0))

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log_run(elapsed_ms, "failed", error=str(last_exc))
        raise RuntimeError(
            f"Failed after {self.max_retries} attempts: {last_exc}"
        ) from last_exc

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure = time.monotonic()
        if self._failure_count >= 5:
            self._circuit_open = True
            logger.warning(
                "Circuit OPEN for %s/%s after %d failures",
                self.integration_id, self.provider, self._failure_count,
            )

    def _log_run(
        self,
        latency_ms: int,
        status: str,
        error: str | None = None,
        record_count: int | None = None,
    ) -> None:
        """Best-effort write to IntegrationRunLog (non-blocking)."""
        try:
            from app.core.database import SessionLocal
            from app.models.market import IntegrationRunLog

            with SessionLocal() as db:
                db.add(IntegrationRunLog(
                    integration_id=self.integration_id,
                    provider=self.provider,
                    operation="call",
                    status=status,
                    latency_ms=latency_ms,
                    attempt_count=self._failure_count or 1,
                    error_message=error,
                    response_record_count=record_count,
                ))
                db.commit()
        except Exception:
            logger.debug("Failed to persist integration run log", exc_info=True)
