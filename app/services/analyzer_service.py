"""HTTP bridge to BOM Intelligence Engine microservice."""
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger("analyzer_service")
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def call_analyzer(file_bytes: bytes, filename: str, user_location: str = "", target_currency: str = "USD") -> dict:
    url = f"{settings.BOM_ANALYZER_URL}/api/analyze-bom"
    headers = {}
    if settings.INTERNAL_API_KEY:
        headers["X-Internal-Key"] = settings.INTERNAL_API_KEY
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            url,
            files={"file": (filename, file_bytes)},
            data={"user_location": user_location, "target_currency": target_currency},
            headers=headers,
        )
        resp.raise_for_status()
    result = resp.json()
    if "components" in result:
        return result
    raise RuntimeError("Invalid analyzer response format")


async def health_check() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {"status": "ok", "detail": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}
