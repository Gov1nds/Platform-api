"""
Pricing Service — DB-First with Smart Matching & Auto-Learning

LOOKUP PRIORITY:
  1. MPN exact match (fastest, most reliable)
  2. Normalized name match (handles synonyms/abbreviations)
  3. External API (Mouser/DigiKey/Misumi)
  4. Fallback estimate (rule-based)

AUTO-LEARNING:
  - Every external API result is saved to DB with normalized key
  - Every fallback estimate is saved (low confidence, gets overwritten)
  - Next lookup for same part or synonym → instant DB hit
  - RFQ feedback overwrites estimates with real costs

DEDUPLICATION:
  - Uses normalized_key (lowercase, stripped, abbreviated-expanded)
  - MPN match takes priority over name match
  - Updates existing record instead of creating duplicates
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.models.pricing import PricingHistory
from app.models.vendor import Vendor
from app.models.memory import SupplierMemory

logger = logging.getLogger("pricing_service")

# ══════════════════════════════════════════════════════════
# NORMALIZATION — same logic as BOM Engine normalizer
# This ensures "Res 10k" and "Resistor 10000" match
# ══════════════════════════════════════════════════════════

_ABBREVS = [
    (re.compile(r"\bres\b", re.I), "resistor"),
    (re.compile(r"\bcap\b", re.I), "capacitor"),
    (re.compile(r"\bind\b", re.I), "inductor"),
    (re.compile(r"\bconn\b", re.I), "connector"),
    (re.compile(r"\bled\b", re.I), "led"),
    (re.compile(r"\bss\b(?=\s)", re.I), "stainless_steel"),
    (re.compile(r"\bal\b(?=\s)", re.I), "aluminum"),
    (re.compile(r"\bpcb\b", re.I), "pcb"),
    (re.compile(r"\bmcu\b", re.I), "microcontroller"),
    (re.compile(r"\bic\b", re.I), "integrated_circuit"),
]

_VALUE_SCALES = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.I), lambda m: str(int(float(m.group(1)) * 1000))),
    (re.compile(r"(\d+(?:\.\d+)?)\s*M\b"), lambda m: str(int(float(m.group(1)) * 1e6))),
]

_BOLT_RE = re.compile(r"\bm(\d+)\s*x\s*(\d+)", re.I)


def _normalize_for_lookup(text: str) -> str:
    """
    Normalize part name for DB matching.
    Mirrors BOM Engine's normalize_text() so both systems produce same keys.

    "Res 10k 5% 0402"         → "resistor 10000 5% 0402"
    "Cap 100nF 16V 0603"      → "capacitor 100nf 16v 0603"
    "M5x20 Hex Bolt SS304"    → "metric_bolt_m5x20 hex bolt stainless_steel 304"
    """
    if not text:
        return ""
    s = text.strip().lower()
    s = _BOLT_RE.sub(r"metric_bolt_m\1x\2", s)
    for pat, repl in _ABBREVS:
        s = pat.sub(repl, s, count=1)
    for pat, fn in _VALUE_SCALES:
        s = pat.sub(fn, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_mpn(mpn: str) -> str:
    """Normalize MPN for exact matching — uppercase, strip whitespace/dashes."""
    if not mpn:
        return ""
    return re.sub(r"[\s\-_]", "", mpn.strip().upper())


# ══════════════════════════════════════════════════════════
# FALLBACK PRICES
# ══════════════════════════════════════════════════════════

_FALLBACK_PRICES = {
    "resistor": 0.01, "capacitor": 0.02, "inductor": 0.08, "diode": 0.05,
    "transistor": 0.10, "integrated_circuit": 2.50, "microcontroller": 5.00,
    "led": 0.03, "connector": 0.50, "relay": 2.00, "fuse": 0.15, "sensor": 3.00,
    "bolt": 0.12, "screw": 0.08, "nut": 0.05, "washer": 0.03,
    "bearing": 3.50, "spring": 0.40, "bracket": 8.00, "housing": 25.00,
    "shaft": 12.00, "plate": 5.00, "sheet": 15.00, "rod": 8.00,
    "pcb": 15.00,
}


# ══════════════════════════════════════════════════════════
# CORE: get_price()
# ══════════════════════════════════════════════════════════

def get_price(part: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """
    DB-first pricing with smart matching.

    Lookup:
      1. MPN exact match (if MPN provided)
      2. Normalized name match in pricing_history
      3. External API query (auto-saved to DB)
      4. Rule-based fallback (auto-saved to DB)
    """
    name = part.get("part_name", part.get("description", part.get("name", "")))
    material = part.get("material", "")
    quantity = part.get("quantity", 1)
    mpn = part.get("mpn", "")

    clean_mpn = _clean_mpn(mpn)
    norm_name = _normalize_for_lookup(name)

    # ── 1. MPN EXACT MATCH ──
    if clean_mpn and len(clean_mpn) >= 3:
        mpn_match = (
            db.query(PricingHistory)
            .filter(
                func.upper(func.replace(PricingHistory.part_name, " ", ""))
                .contains(clean_mpn)
            )
            .order_by(desc(PricingHistory.recorded_at))
            .first()
        )
        if mpn_match and mpn_match.price > 0:
            return {
                "price": mpn_match.price,
                "source": "mpn_match",
                "confidence": "high",
                "match_key": clean_mpn,
                "vendor_id": mpn_match.vendor_id,
                "region": mpn_match.region or "",
            }

    # ── 2. NORMALIZED NAME MATCH ──
    if norm_name and len(norm_name) >= 3:
        # Exact normalized match
        name_match = (
            db.query(PricingHistory)
            .filter(func.lower(PricingHistory.part_name) == norm_name)
            .order_by(desc(PricingHistory.recorded_at))
            .first()
        )

        # Partial match (first 3 significant words)
        if not name_match:
            words = [w for w in norm_name.split() if len(w) > 2][:3]
            if words:
                partial_key = " ".join(words)
                name_match = (
                    db.query(PricingHistory)
                    .filter(func.lower(PricingHistory.part_name).contains(partial_key))
                    .order_by(desc(PricingHistory.recorded_at))
                    .first()
                )

        if name_match and name_match.price > 0:
            source_type = name_match.process or "db_match"
            confidence = "high" if source_type in ("external_api", "rfq_actual") else "medium"
            return {
                "price": name_match.price,
                "source": source_type,
                "confidence": confidence,
                "match_key": norm_name,
            }

    # ── 3. EXTERNAL API ──
    ext_price = _query_external_api(name, mpn, quantity)
    if ext_price and ext_price > 0:
        logger.info(f"get_price '{name}': external API → ${ext_price}")
        _save_price(db, norm_name, mpn, material, quantity, ext_price, source="external_api")
        return {
            "price": ext_price,
            "source": "external_api",
            "confidence": "medium",
        }

    # ── 4. FALLBACK ──
    fallback = _estimate_fallback_price(name, material, quantity)
    _save_price(db, norm_name, mpn, material, quantity, fallback, source="fallback_estimate")
    return {
        "price": fallback,
        "source": "fallback_estimate",
        "confidence": "low",
    }


# ══════════════════════════════════════════════════════════
# AUTO-SAVE WITH DEDUPLICATION
# ══════════════════════════════════════════════════════════

# Source priority: higher number = more trustworthy
_SOURCE_PRIORITY = {"fallback_estimate": 1, "external_api": 2, "rfq_actual": 3}


def _save_price(db: Session, norm_name: str, mpn: str, material: str,
                quantity: int, price: float, source: str = ""):
    """
    Save price to DB with deduplication.

    - Uses normalized name as storage key
    - If same name exists: higher-priority source overwrites lower
    - Same source → updates price (no duplicates)
    """
    try:
        vendor = db.query(Vendor).first()
        if not vendor:
            return

        store_name = norm_name or _normalize_for_lookup(mpn)
        if not store_name:
            return

        existing = (
            db.query(PricingHistory)
            .filter(func.lower(PricingHistory.part_name) == store_name.lower())
            .order_by(desc(PricingHistory.recorded_at))
            .first()
        )

        if existing:
            existing_priority = _SOURCE_PRIORITY.get(existing.process, 0)
            new_priority = _SOURCE_PRIORITY.get(source, 0)

            if new_priority >= existing_priority:
                existing.price = round(price, 6)
                existing.process = source
                existing.quantity = quantity
                if material:
                    existing.material = material[:255]
                existing.recorded_at = datetime.utcnow()
                db.commit()
            return

        db.add(PricingHistory(
            vendor_id=vendor.id,
            part_name=store_name,
            material=material[:255] if material else "",
            quantity=quantity,
            price=round(price, 6),
            currency="USD",
            region=vendor.region or "",
            process=source,
        ))
        db.commit()

    except Exception as e:
        logger.warning(f"Save pricing failed for '{norm_name}': {e}")
        try:
            db.rollback()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
# EXTERNAL API + FALLBACK
# ══════════════════════════════════════════════════════════

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
    nl = _normalize_for_lookup(name)
    base = 12.0

    for kw, price in _FALLBACK_PRICES.items():
        if kw in nl:
            base = price
            break

    ml = material.lower() if material else ""
    if any(w in ml for w in ["stainless", "ss304", "ss316"]):
        base *= 1.8
    elif any(w in ml for w in ["titanium"]):
        base *= 5.0
    elif any(w in ml for w in ["aluminum", "6061", "7075"]):
        base *= 1.3
    elif any(w in ml for w in ["copper", "brass"]):
        base *= 2.0

    if quantity >= 1000:
        base *= 0.5
    elif quantity >= 100:
        base *= 0.7
    elif quantity >= 10:
        base *= 0.85

    return round(max(0.01, base), 4)


def _detect_category(name: str) -> str:
    nl = name.lower() if name else ""
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

        ext = external_pricing.get(item_id, {})
        if ext.get("best_price"):
            comp["_price"] = ext["best_price"]
            comp["_price_source"] = "external_api"
            comp["_price_confidence"] = ext.get("confidence", "medium")
            continue

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
            if not mpn and not name:
                continue
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
    """Manual pricing record (from RFQ completion, admin input, etc.)."""
    entry = PricingHistory(
        vendor_id=vendor_id, part_name=part_name, material=material,
        process=process, quantity=quantity, price=price,
        currency=currency, region=region,
    )
    db.add(entry)
    db.commit()
    return entry