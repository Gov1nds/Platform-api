"""
HTTP bridge to BOM Intelligence Engine microservice.

Provides both:
- Legacy monolithic call_analyzer() for backward compatibility
- Decomposed per-line calls (normalize, enrich, score, strategy) per GAP-002

References: GAP-002, architecture.md CC-14, api-contract-review Section 6
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("analyzer_service")

TIMEOUT_DEFAULT = httpx.Timeout(120.0, connect=10.0)
TIMEOUT_NORMALIZE = httpx.Timeout(0.5, connect=5.0)
TIMEOUT_ENRICH = httpx.Timeout(1.0, connect=5.0)
TIMEOUT_SCORE = httpx.Timeout(0.5, connect=5.0)
TIMEOUT_STRATEGY = httpx.Timeout(1.0, connect=5.0)

_MAX_RETRIES = 3


def _headers(trace_id: str | None = None) -> dict[str, str]:
    h: dict[str, str] = {}
    if settings.INTERNAL_API_KEY:
        h["X-Internal-Key"] = settings.INTERNAL_API_KEY
    if trace_id:
        h["X-Trace-ID"] = trace_id
    return h


# ── Legacy monolithic call (retained) ────────────────────────────────────────

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


# ── Decomposed per-line calls (GAP-002) ──────────────────────────────────────

async def call_normalize(
    bom_line_data: dict,
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/normalize — per-line normalization.

    Input:  {bom_line_id, raw_text, description, quantity, unit, specs}
    Output: {normalized_text, canonical_part_key, classification_confidence,
             category_code, procurement_class, ambiguity_flags,
             split_merge_proposal, normalization_trace}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/normalize"
    return await _call_with_retry(url, bom_line_data, TIMEOUT_NORMALIZE, trace_id)


async def call_enrich(
    bom_line_data: dict,
    market_data: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/enrich — per-line enrichment.

    Input:  {bom_line_id, normalized_data, market_data}
    Output: {price_band, tariff_data, logistics_data, risk_flags,
             data_freshness_summary}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/enrich"
    payload = {**bom_line_data}
    if market_data:
        payload["market_data"] = market_data
    return await _call_with_retry(url, payload, TIMEOUT_ENRICH, trace_id)


async def call_score(
    bom_line_data: dict,
    enrichment: dict,
    vendor_candidates: list[dict],
    weight_profile: str = "balanced",
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/score — per-line vendor scoring.

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
    return await _call_with_retry(url, payload, TIMEOUT_SCORE, trace_id)


async def call_strategy(
    bom_lines: list[dict],
    scores: list[dict],
    trace_id: str | None = None,
) -> dict:
    """
    POST /api/strategy — multi-line strategy analysis.

    Input:  {bom_lines[], scores[]}
    Output: {consolidation_opportunities, sourcing_mode_recommendations}
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/strategy"
    payload = {"bom_lines": bom_lines, "scores": scores}
    return await _call_with_retry(url, payload, TIMEOUT_STRATEGY, trace_id)


# ── Health check ─────────────────────────────────────────────────────────────

async def health_check() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {"status": "ok", "detail": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


# ── Internal retry helper ────────────────────────────────────────────────────

async def _call_with_retry(
    url: str,
    payload: dict,
    timeout: httpx.Timeout,
    trace_id: str | None = None,
) -> dict:
    """POST with simple retry (up to _MAX_RETRIES attempts)."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=_headers(trace_id))
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException as e:
            logger.warning("Timeout on %s (attempt %d/%d)", url, attempt, _MAX_RETRIES)
            last_exc = e
        except httpx.HTTPStatusError as e:
            logger.warning("HTTP %d on %s (attempt %d/%d)", e.response.status_code, url, attempt, _MAX_RETRIES)
            if e.response.status_code < 500:
                raise  # Don't retry client errors
            last_exc = e
        except Exception as e:
            logger.warning("Error calling %s: %s (attempt %d/%d)", url, e, attempt, _MAX_RETRIES)
            last_exc = e

    raise RuntimeError(f"Failed after {_MAX_RETRIES} attempts to {url}: {last_exc}") from last_exc