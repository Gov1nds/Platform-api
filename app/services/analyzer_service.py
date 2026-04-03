"""
Analyzer Service — HTTP Bridge to BOM Analyzer Microservice.

Supports:
- File upload (call_analyzer)
- File path async call (analyze_bom)

Handles BOTH response formats:
  - v3: { "components": [...], "summary": {...} }
  - v2: { "section_2_component_breakdown": [...] }

Transforms v2 → v3 for platform consistency.
"""

import logging
import httpx
import os
import asyncio
from pathlib import Path
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger("analyzer_service")

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

ENGINE_URL = os.getenv("BOM_ENGINE_URL") or settings.BOM_ANALYZER_URL
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY") or settings.INTERNAL_API_KEY

TIMEOUT = httpx.Timeout(120.0, connect=10.0)


# -------------------------------------------------------------------
# NEW: Async path-based analyzer call (worker-safe)
# -------------------------------------------------------------------

async def analyze_bom(
    file_path: str,
    user_location: str,
    target_currency: str,
    email: str,
) -> Dict[str, Any]:
    """
    Async-safe analyzer bridge that preserves the existing call signature,
    but forwards the actual file bytes using the engine's multipart contract.
    """

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"BOM file not found: {file_path}")

    file_bytes = path.read_bytes()
    filename = path.name

    return await call_analyzer(
        file_bytes=file_bytes,
        filename=filename,
        user_location=user_location,
        target_currency=target_currency,
    )


# -------------------------------------------------------------------
# EXISTING: File upload analyzer call (PRESERVED + HARDENED)
# -------------------------------------------------------------------

async def call_analyzer(
    file_bytes: bytes,
    filename: str,
    user_location: str = "",
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """
    Forward raw BOM file to BOM Analyzer service.
    Handles v2/v3 response formats.
    """

    url = f"{ENGINE_URL}/api/analyze-bom"

    headers = {}
    if INTERNAL_API_KEY:
        headers["X-Internal-Key"] = INTERNAL_API_KEY

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
    return _normalize_response(result)


def call_analyzer_sync(
    file_bytes: bytes,
    filename: str,
    user_location: str = "",
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """
    Synchronous convenience wrapper used by the intake pipeline.
    Keeps the actual HTTP contract identical to call_analyzer().
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            call_analyzer(
                file_bytes=file_bytes,
                filename=filename,
                user_location=user_location,
                target_currency=target_currency,
            )
        )

    if loop.is_running():
        # Avoid nested loop failures in worker contexts.
        import threading

        result: Dict[str, Any] = {}
        error: list[BaseException] = []

        def _runner():
            try:
                result["value"] = asyncio.run(
                    call_analyzer(
                        file_bytes=file_bytes,
                        filename=filename,
                        user_location=user_location,
                        target_currency=target_currency,
                    )
                )
            except BaseException as exc:  # pragma: no cover - defensive
                error.append(exc)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result["value"]

    return loop.run_until_complete(
        call_analyzer(
            file_bytes=file_bytes,
            filename=filename,
            user_location=user_location,
            target_currency=target_currency,
        )
    )





def call_analyzer_sync(
    file_bytes: bytes,
    filename: str,
    user_location: str = "",
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """
    Synchronous bridge to the engine using the canonical multipart upload contract.
    """
    url = f"{ENGINE_URL}/api/analyze-bom"
    headers = {}
    if INTERNAL_API_KEY:
        headers["X-Internal-Key"] = INTERNAL_API_KEY

    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            resp = client.post(
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

    return _normalize_response(resp.json())

# -------------------------------------------------------------------
# NORMALIZATION (SHARED)
# -------------------------------------------------------------------

def _normalize_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize analyzer response to v3 format.
    """

    # v3 format
    if "components" in result:
        logger.info(f"Analyzer v3: {len(result['components'])} components")
        return result

    # v2 format
    if "section_2_component_breakdown" in result:
        logger.info("Analyzer v2 detected — transforming")

        components = _transform_v2_to_v3(result)

        return {
            "components": components,
            "summary": {
                "total_items": len(components),
                "categories": _count_categories(components),
            },
            "_meta": result.get("_meta", {}),
            "_v2_full_report": result,
        }

    raise RuntimeError("Invalid analyzer response — unknown format")


# -------------------------------------------------------------------
# V2 → V3 TRANSFORM (UNCHANGED LOGIC)
# -------------------------------------------------------------------

def _transform_v2_to_v3(v2_result: Dict) -> list:
    s2 = v2_result.get("section_2_component_breakdown", [])
    components = []

    for item in s2:
        comp = {
            "item_id": item.get("item_id", ""),
            "raw_text": item.get("description", ""),
            "standard_text": item.get("description", ""),
            "description": item.get("description", ""),
            "quantity": item.get("quantity", 1),
            "part_number": item.get("mpn", ""),
            "mpn": item.get("mpn", ""),
            "manufacturer": item.get("manufacturer", ""),
            "material": item.get("material", ""),
            "notes": item.get("notes", ""),
            "unit": "each",

            "category": item.get("category", "standard"),
            "classification_path": item.get("classification_path", "3_1"),
            "classification_confidence": item.get("confidence", 0),
            "classification_reason": item.get("classification_reason", ""),

            "has_mpn": bool(item.get("mpn")),
            "has_brand": bool(item.get("manufacturer")),
            "is_generic": item.get("is_generic", False),
            "is_raw": item.get("is_raw", False),
            "is_custom": item.get("is_custom", False),

            "material_form": item.get("material_form"),
            "geometry": item.get("geometry"),
            "tolerance": item.get("tolerance"),
            "secondary_ops": item.get("secondary_ops", []),

            "specs": item.get("specs", {}),

            "procurement_class": item.get("procurement_class", "catalog_purchase"),
            "rfq_required": item.get("rfq_required", False),
            "drawing_required": item.get("drawing_required", False),

            "part_type": "custom" if item.get("is_custom") else "standard",

            "canonical_part_key": item.get("canonical_part_key", ""),
        }

        components.append(comp)

    return components


def _count_categories(components: list) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for c in components:
        cat = c.get("category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1
    return counts


# -------------------------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------------------------

async def health_check() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ENGINE_URL}/health")
            return {"status": "ok", "analyzer": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}