"""
HTTP bridge to BOM Intelligence Engine microservice.

Provides both:
- Legacy monolithic call_analyzer() for backward compatibility
- Decomposed per-line calls (normalize, enrich, score, strategy) per GAP-002

All decomposed calls include:
- Retry with exponential backoff (manual implementation)
- Circuit breaker pattern (in-process state tracking)
- Timeout configuration per endpoint
- X-Internal-Key and X-Trace-ID header propagation

References: GAP-002, architecture.md CC-14, api-contract-review Section 6
"""
from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock

import httpx

from app.core.config import settings

logger = logging.getLogger("analyzer_service")

TIMEOUT_DEFAULT = httpx.Timeout(120.0, connect=10.0)
TIMEOUT_NORMALIZE = httpx.Timeout(0.5, connect=5.0)
TIMEOUT_ENRICH = httpx.Timeout(1.0, connect=5.0)
TIMEOUT_SCORE = httpx.Timeout(0.5, connect=5.0)
TIMEOUT_STRATEGY = httpx.Timeout(1.0, connect=5.0)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 10.0


# -- Circuit Breaker ----------------------------------------------------------

class CircuitBreaker:
    """
    Simple in-process circuit breaker.

    States:
      CLOSED    -- normal operation, requests pass through
      OPEN      -- too many failures, requests rejected immediately
      HALF_OPEN -- recovery test, single request allowed through

    Opens after failure_threshold consecutive failures.
    Transitions to HALF_OPEN after recovery_timeout seconds.
    A single success in HALF_OPEN closes the circuit.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._state = self.CLOSED
        self._last_failure_time: float = 0.0
        self._lock = Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._state = self.OPEN
                logger.warning(
                    "Circuit breaker OPEN after %d failures (threshold=%d)",
                    self._failure_count, self._failure_threshold,
                )

    def allow_request(self) -> bool:
        s = self.state
        return s in (self.CLOSED, self.HALF_OPEN)


# Per-endpoint circuit breakers
_cb_normalize = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
_cb_enrich = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
_cb_score = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
_cb_strategy = CircuitBreaker(failure_threshold=5, recovery_timeout=30)


def _headers(trace_id: str | None = None) -> dict[str, str]:
    h: dict[str, str] = {}
    if settings.INTERNAL_API_KEY:
        h["X-Internal-Key"] = settings.INTERNAL_API_KEY
    if trace_id:
        h["X-Trace-ID"] = trace_id
    return h


# -- Legacy monolithic call (retained) ----------------------------------------

async def call_analyzer(
    file_bytes: bytes,
    filename: str,
    user_location: str = "",
    target_currency: str = "USD",
) -> dict:
    """Full-file upload to /api/analyze-bom. Legacy path."""
    url = f"{settings.BOM_ANALYZER_URL}/api/analyze-bom"
    async with httpx.AsyncClient(timeout=TIMEOUT_DEFAULT) as client:
        resp = await client.post(
            url,
            files={"file": (filename, file_bytes)},
            data={"user_location": user_location, "target_currency": target_currency},
            headers=_headers(),
        )
        resp.raise_for_status()
    result = resp.json()
    if "components" in result:
        return result
    raise RuntimeError("Invalid analyzer response format")


# -- Decomposed per-line calls (GAP-002) --------------------------------------

async def call_normalize(
    bom_line_data: dict,
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/normalize -- per-line normalization.

    Input:  {bom_line_id, raw_text, description, quantity, unit, specs}
    Output: {normalized_text, canonical_part_key, classification_confidence,
             category_code, procurement_class, ambiguity_flags,
             split_merge_proposal, normalization_trace}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/normalize"
    return await _call_with_circuit_breaker(
        url, bom_line_data, TIMEOUT_NORMALIZE, _cb_normalize, trace_id,
    )


async def call_enrich(
    bom_line_data: dict,
    market_data: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/enrich -- per-line enrichment.

    Input:  {bom_line_id, normalized_data, market_data}
    Output: {price_band, tariff_data, logistics_data, risk_flags,
             data_freshness_summary}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/enrich"
    payload = {**bom_line_data}
    if market_data:
        payload["market_data"] = market_data
    return await _call_with_circuit_breaker(
        url, payload, TIMEOUT_ENRICH, _cb_enrich, trace_id,
    )


async def call_score(
    bom_line_data: dict,
    enrichment: dict,
    vendor_candidates: list[dict],
    weight_profile: str = "balanced",
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/score -- per-line vendor scoring.

    Input:  {bom_line_id, enrichment, vendor_candidates, weight_profile}
    Output: {vendor_scores[], tlc_breakdown, strategy_recommendation,
             substitution_candidates, elimination_reasons, evidence_records}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/score"
    payload = {
        **bom_line_data,
        "enrichment": enrichment,
        "vendor_candidates": vendor_candidates,
        "weight_profile": weight_profile,
    }
    return await _call_with_circuit_breaker(
        url, payload, TIMEOUT_SCORE, _cb_score, trace_id,
    )


async def call_strategy(
    bom_lines: list[dict],
    scores: list[dict],
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/strategy -- multi-line strategy analysis.

    Input:  {bom_lines[], scores[]}
    Output: {consolidation_opportunities, sourcing_mode_recommendations}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/strategy"
    payload = {"bom_lines": bom_lines, "scores": scores}
    return await _call_with_circuit_breaker(
        url, payload, TIMEOUT_STRATEGY, _cb_strategy, trace_id,
    )


# -- Health check -------------------------------------------------------------

async def health_check() -> dict:
    """Check engine health and report circuit breaker states."""
    cb_states = {
        "normalize": _cb_normalize.state,
        "enrich": _cb_enrich.state,
        "score": _cb_score.state,
        "strategy": _cb_strategy.state,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {
                "status": "ok",
                "detail": r.json(),
                "circuit_breakers": cb_states,
            }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "circuit_breakers": cb_states,
        }


# -- Internal helpers ---------------------------------------------------------

async def _call_with_circuit_breaker(
    url: str,
    payload: dict,
    timeout: httpx.Timeout,
    cb: CircuitBreaker,
    trace_id: str | None = None,
) -> dict:
    """
    POST with circuit breaker + retry with exponential backoff.

    - If circuit is OPEN, raises immediately without making a request.
    - Retries up to _MAX_RETRIES times with exponential backoff.
    - Records success/failure on the circuit breaker per attempt.
    - Does NOT retry 4xx client errors (raises immediately).
    """
    if not cb.allow_request():
        raise RuntimeError(f"Circuit breaker OPEN for {url}")

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=_headers(trace_id))
                resp.raise_for_status()
                cb.record_success()
                return resp.json()
        except httpx.TimeoutException as e:
            logger.warning("Timeout on %s (attempt %d/%d)", url, attempt, _MAX_RETRIES)
            cb.record_failure()
            last_exc = e
        except httpx.HTTPStatusError as e:
            logger.warning(
                "HTTP %d on %s (attempt %d/%d)",
                e.response.status_code, url, attempt, _MAX_RETRIES,
            )
            if e.response.status_code < 500:
                cb.record_failure()
                raise  # Don't retry client errors
            cb.record_failure()
            last_exc = e
        except Exception as e:
            logger.warning("Error calling %s: %s (attempt %d/%d)", url, e, attempt, _MAX_RETRIES)
            cb.record_failure()
            last_exc = e

        # Exponential backoff before next attempt
        if attempt < _MAX_RETRIES:
            backoff = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed after {_MAX_RETRIES} attempts to {url}: {last_exc}") from last_exc
