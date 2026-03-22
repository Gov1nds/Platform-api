"""
Analyzer Service v2 — HTTP Bridge to BOM Analyzer Microservice.

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
    Returns the full 7-section analysis report.
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
    if "section_1_executive_summary" not in result:
        raise RuntimeError("Invalid analyzer response")

    return result


async def health_check() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {"status": "ok", "analyzer": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}
