"""
Pricing Service FINAL — DB-First Data Model

MANDATORY PRIORITY:
  1. REAL VENDOR DATA (latest from pricing_history)
  2. HISTORICAL AVERAGE (all matching records)
  3. EXTERNAL API (Mouser/DigiKey/Misumi)
  4. FALLBACK (rule-based estimate)

get_price() is the CORE function used by strategy engine.
"""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.pricing import PricingHistory
from app.models.vendor import Vendor
from app.models.memory import SupplierMemory

logger = logging.getLogger("pricing_service")

# Fallback base prices by keyword
_FALLBACK_PRICES = {
    "resistor": 0.01, "capacitor": 0.02, "inductor": 0.08, "diode": 0.05,
    "transistor": 0.10, "ic": 2.50, "microcontroller": 5.00, "led": 0.03,
    "connector": 0.50, "relay": 2.00, "fuse": 0.15, "sensor": 3.00,
    "bolt": 0.12, "screw": 0.08, "nut": 0.05, "washer": 0.03,
    "bearing": 3.50, "spring": 0.40, "bracket": 8.00, "housing": 25.00,
    "shaft": 12.00, "plate": 5.00, "sheet": 15.00, "rod": 8.00,
}


# ══════════════════════════════════════════════════════════
# CORE: get_price() — EXACT IMPLEMENTATION AS REQUIRED
# ══════════════════════════════════════════════════════════

def get_price(part: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """
    DB-first pricing. Returns price + source + confidence.

    Priority:
      1. Latest real vendor price from pricing_history
      2. Historical average from pricing_history
      3. External API query
      4. Rule-based fallback
    """
    name = part.get("part_name", part.get("description", part.get("name", "")))
    material = part.get("material", "")
    quantity = part.get("quantity", 1)
    mpn = part.get("mpn", "")

    # ── 1. REAL VENDOR DATA (latest record) ──
    latest = (
        db.query(PricingHistory)
        .filter(PricingHistory.part_name.ilike(f"%{name[:50]}%"))
        .order_by(PricingHistory.recorded_at.desc())
        .first()
    ) if name else None

    if latest and latest.price > 0:
        logger.debug(f"get_price '{name}': real vendor ${latest.price}")
        return {
            "price": latest.price,
            "source": "real_vendor_data",
            "confidence": "high",
            "vendor_id": latest.vendor_id,
            "region": latest.region or "",
            "recorded_at": str(latest.recorded_at) if latest.recorded_at else "",
        }

    # ── 2. HISTORICAL AVERAGE ──
    history = (
        db.query(PricingHistory)
        .filter(PricingHistory.part_name.ilike(f"%{name[:50]}%"))
        .all()
    ) if name else []

    if history:
        prices = [p.price for p in history if p.price and p.price > 0]
        if prices:
            avg = sum(prices) / len(prices)
            logger.debug(f"get_price '{name}': historical avg ${avg:.2f} ({len(prices)} records)")
            return {
                "price": round(avg, 4),
                "source": "historical_average",
                "confidence": "medium" if len(prices) >= 3 else "low",
                "sample_count": len(prices),
            }

    # ── 3. EXTERNAL API ──
    ext_price = _query_external_api(name, mpn, quantity)
    if ext_price and ext_price > 0:
        logger.debug(f"get_price '{name}': external API ${ext_price}")
        return {
            "price": ext_price,
            "source": "external_api",
            "confidence": "medium",
        }

    # ── 4. FALLBACK ──
    fallback = _estimate_fallback_price(name, material, quantity)
    logger.debug(f"get_price '{name}': fallback ${fallback}")
    return {
        "price": fallback,
        "source": "fallback_estimate",
        "confidence": "low",
    }


def _query_external_api(name: str, mpn: str, quantity: int) -> Optional[float]:
    """Try external supplier APIs."""
    try:
        from app.integrations.supplier_router import route_query
        from app.integrations.pricing_aggregator import aggregate_pricing
        category = _detect_category(name)
        results = route_query(name, category=category, mpn=mpn, quantity=quantity)
        agg = aggregate_pricing(results)
        return agg.get("best_price")
    except Exception as e:
        logger.debug(f"External API failed for '{name}': {e}")
        return None


def _estimate_fallback_price(name: str, material: str, quantity: int) -> float:
    """Rule-based fallback pricing."""
    nl = name.lower()
    base = 12.0  # default per prompt spec

    for kw, price in _FALLBACK_PRICES.items():
        if kw in nl:
            base = price
            break

    # Material adjustment
    ml = material.lower()
    if any(w in ml for w in ["stainless", "ss304", "ss316"]):
        base *= 1.8
    elif any(w in ml for w in ["titanium"]):
        base *= 5.0
    elif any(w in ml for w in ["aluminum", "6061", "7075"]):
        base *= 1.3
    elif any(w in ml for w in ["copper", "brass"]):
        base *= 2.0

    # Volume discount
    if quantity >= 1000:
        base *= 0.5
    elif quantity >= 100:
        base *= 0.7
    elif quantity >= 10:
        base *= 0.85

    return round(max(0.01, base), 4)


def _detect_category(name: str) -> str:
    nl = name.lower()
    for cat in ["resistor", "capacitor", "inductor", "ic", "led", "connector",
                 "diode", "transistor", "bearing", "bolt", "screw", "nut",
                 "washer", "sensor", "relay", "switch", "motor"]:
        if cat in nl:
            return cat
    return ""


# ══════════════════════════════════════════════════════════
# ENRICHMENT + HISTORY
# ══════════════════════════════════════════════════════════

def get_all_pricing_history(db: Session, limit: int = 200) -> List[Dict]:
    entries = db.query(PricingHistory).order_by(PricingHistory.recorded_at.desc()).limit(limit).all()
    return [{"vendor_id": e.vendor_id, "part_name": e.part_name, "material": e.material,
             "process": e.process, "quantity": e.quantity, "price": e.price,
             "region": e.region or ""} for e in entries]


def enrich_analysis_with_pricing(analyzer_output: Dict, db: Session,
                                  external_pricing: Optional[Dict] = None) -> Dict:
    """Enrich each component with DB-first pricing."""
    external_pricing = external_pricing or {}
    components = analyzer_output.get("section_2_component_breakdown", [])

    for comp in components:
        item_id = comp.get("item_id", "")
        part = {
            "part_name": comp.get("description", ""),
            "material": comp.get("material", ""),
            "quantity": comp.get("quantity", 1),
            "mpn": comp.get("mpn", ""),
        }

        # External override first
        ext = external_pricing.get(item_id, {})
        if ext.get("best_price"):
            comp["_price"] = ext["best_price"]
            comp["_price_source"] = "external_api"
            comp["_price_confidence"] = ext.get("confidence", "medium")
            continue

        # DB-first pricing
        price_data = get_price(part, db)
        comp["_price"] = price_data["price"]
        comp["_price_source"] = price_data["source"]
        comp["_price_confidence"] = price_data["confidence"]

    return analyzer_output


def fetch_external_pricing(parts: List[Dict]) -> Dict[str, Dict]:
    """Query external APIs for standard parts."""
    results = {}
    try:
        from app.integrations.supplier_router import route_query
        from app.integrations.pricing_aggregator import aggregate_pricing
        for i, part in enumerate(parts):
            mpn = part.get("mpn", "")
            name = part.get("part_name", "")
            qty = part.get("quantity", 1)
            if not mpn and not name: continue
            category = _detect_category(name)
            try:
                api_results = route_query(name, category=category, mpn=mpn, quantity=qty)
                aggregated = aggregate_pricing(api_results)
                if aggregated.get("best_price"):
                    results[f"BOM-{i+1:04d}"] = aggregated
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"External pricing fetch failed: {e}")
    return results


def record_pricing(db: Session, vendor_id: str, part_name: str, price: float,
                   material: str = "", process: str = "", quantity: int = 1,
                   region: str = "", currency: str = "USD"):
    entry = PricingHistory(
        vendor_id=vendor_id, part_name=part_name, material=material,
        process=process, quantity=quantity, price=price,
        currency=currency, region=region,
    )
    db.add(entry)
    db.commit()
    return entry
