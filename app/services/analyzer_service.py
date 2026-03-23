"""
Analyzer Service v3 — HTTP Bridge to BOM Analyzer Microservice.

BOM Engine v3 returns: { components, summary, _meta }
(No longer returns section_1 through section_7 reports)

All communication to BOM Analyzer happens via HTTP.
Includes retry logic, health checks, and response validation.
"""

import logging
import httpx
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger("analyzer_service")
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def call_analyzer(
    file_bytes: bytes,
    filename: str,
    user_location: str = "",
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """
    Forward raw BOM file to BOM Analyzer service.

    Returns normalized + classified components with extracted specs.
    Response shape:
    {
        "components": [ { item_id, description, category, specs, ... }, ... ],
        "summary": { total_items, categories: { standard, custom, raw_material, unknown } },
        "_meta": { total_time_s, version }
    }
    """
    url = f"{settings.BOM_ANALYZER_URL}/api/analyze-bom"
    headers = {}
    if settings.INTERNAL_API_KEY:
        headers["X-Internal-Key"] = settings.INTERNAL_API_KEY

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                url,
                files={"file": (filename, file_bytes)},
                data={
                    "user_location": user_location,
                    "target_currency": target_currency,
                },
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.error("BOM Analyzer timed out")
            raise RuntimeError("Analysis timed out. Try a smaller BOM file.")
        except httpx.HTTPStatusError as e:
            logger.error(f"Analyzer HTTP {e.response.status_code}")
            raise RuntimeError(f"Analysis service error: {e.response.status_code}")
        except httpx.ConnectError:
            logger.error("Cannot reach BOM Analyzer")
            raise RuntimeError("Analysis service unavailable")

    result = resp.json()

    # ── Validate v3 response schema ──
    if "components" not in result:
        # Backward compat: if old v2 engine returns section_1, reject it
        if "section_1_executive_summary" in result:
            logger.error(
                "BOM Analyzer returned v2 format (section_1). "
                "Please upgrade BOM Engine to v3."
            )
            raise RuntimeError(
                "BOM Analyzer version mismatch — expected v3 format"
            )
        raise RuntimeError("Invalid analyzer response — missing 'components'")

    if not isinstance(result["components"], list):
        raise RuntimeError("Invalid analyzer response — 'components' must be a list")

    logger.info(
        f"Analyzer returned {len(result['components'])} components "
        f"in {result.get('_meta', {}).get('total_time_s', '?')}s"
    )

    return result


async def health_check() -> Dict[str, Any]:
    """Check if BOM Analyzer is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {"status": "ok", "analyzer": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}
