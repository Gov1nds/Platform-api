"""
Analyzer Service — HTTP Bridge to BOM Analyzer Microservice.

Handles BOTH response formats:
  - v3: { "components": [...], "summary": {...} }
  - v2: { "section_1_executive_summary": {...}, "section_2_component_breakdown": [...], ... }

If v2 response is detected, it transforms it into v3 format so the rest
of Platform API (bom_service, routes/bom) can work with a single schema.
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
    Returns normalized data — auto-detects v2 or v3 response format.
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

    # ── v3 format: already has "components" ──
    if "components" in result:
        logger.info(f"Analyzer returned v3 format: {len(result['components'])} components")
        return result

    # ── v2 format: has "section_1_executive_summary" — transform to v3 ──
    if "section_2_component_breakdown" in result:
        logger.info("Analyzer returned v2 format — transforming to v3")
        components = _transform_v2_to_v3(result)
        return {
            "components": components,
            "summary": {
                "total_items": len(components),
                "categories": _count_categories(components),
            },
            "_meta": result.get("_meta", {}),
            "_v2_full_report": result,  # keep original for enriched output
        }

    raise RuntimeError("Invalid analyzer response — unrecognized format")


def _transform_v2_to_v3(v2_result: Dict) -> list:
    """
    Transform v2 section_2_component_breakdown items into v3 component format.
    """
    s2 = v2_result.get("section_2_component_breakdown", [])
    components = []

    for item in s2:
        comp = {
            # Identity
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
            # Classification
            "category": item.get("category", "standard"),
            "classification_path": item.get("classification_path", "3_1"),
            "classification_confidence": item.get("confidence", 0),
            "classification_reason": item.get("classification_reason", ""),
            "has_mpn": bool(item.get("mpn")),
            "has_brand": bool(item.get("manufacturer")),
            "is_generic": item.get("is_generic", False),
            "is_raw": item.get("is_raw", False),
            "is_custom": item.get("is_custom", False),
            # Manufacturing attributes
            "material_form": item.get("material_form"),
            "geometry": item.get("geometry"),
            "tolerance": item.get("tolerance"),
            "secondary_ops": item.get("secondary_ops", []),
            # Specs
            "specs": item.get("specs", {}),
            # Procurement intent
            "procurement_class": item.get("procurement_class", "catalog_purchase"),
            "rfq_required": item.get("rfq_required", False),
            "drawing_required": item.get("drawing_required", False),
            "part_type": "custom" if item.get("is_custom", False) else "standard",
            # Canonical key (from engine v4.1+)
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


async def health_check() -> Dict[str, Any]:
    """Check if BOM Analyzer is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            return {"status": "ok", "analyzer": r.json()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}