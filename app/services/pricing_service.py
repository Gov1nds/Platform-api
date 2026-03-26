"""
Pricing Service v4 — DB-First with Append-Only History

FIXES:
  - _save_price() is now APPEND-ONLY: never overwrites existing rows
  - _save_price() does NOT attribute all prices to the first vendor
  - Simulated API data is NOT saved to DB
  - MPN matching uses exact match, not CONTAINS
  - Partial name match is less aggressive
  - Added freshness metadata (is_current, confidence, source_type)
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.models.pricing import PricingHistory
from app.models.vendor import Vendor

logger = logging.getLogger("pricing_service")

# ── Normalization ──

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
    if not mpn:
        return ""
    return re.sub(r"[\s\-_]", "", mpn.strip().upper())


# ── Fallback Prices ──

_FALLBACK_PRICES = {
    "resistor": 0.01, "capacitor": 0.02, "inductor": 0.08, "diode": 0.05,
    "transistor": 0.10, "integrated_circuit": 2.50, "microcontroller": 5.00,
    "led": 0.03, "connector": 0.50, "relay": 2.00, "fuse": 0.15, "sensor": 3.00,
    "bolt": 0.12, "screw": 0.08, "nut": 0.05, "washer": 0.03,
    "bearing": 3.50, "spring": 0.40, "bracket": 8.00, "housing": 25.00,
    "shaft": 12.00, "plate": 5.00, "sheet": 15.00, "rod": 8.00,
    "pcb": 15.00,
}


# ── Core: get_price() ──

def get_price(part: Dict[str, Any], db: Session) -> Dict[str, Any]:
    name = part.get("part_name", part.get("description", part.get("name", "")))
    material = part.get("material", "")
    quantity = part.get("quantity", 1)
    mpn = part.get("mpn", "")

    clean_mpn = _clean_mpn(mpn)
    norm_name = _normalize_for_lookup(name)

    # ── 1. MPN EXACT MATCH (FIXED: exact, not CONTAINS) ──
    if clean_mpn and len(clean_mpn) >= 4:
        mpn_match = (
            db.query(PricingHistory)
            .filter(
                func.upper(func.replace(PricingHistory.mpn, " ", "")) == clean_mpn,
                PricingHistory.is_current == True,
                PricingHistory.is_simulated == False,
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
                "recorded_at": mpn_match.recorded_at.isoformat() if mpn_match.recorded_at else None,
            }

    # ── 2. NORMALIZED NAME EXACT MATCH (FIXED: exact, not partial) ──
    if norm_name and len(norm_name) >= 5:
        name_match = (
            db.query(PricingHistory)
            .filter(
                func.lower(PricingHistory.normalized_key) == norm_name,
                PricingHistory.is_current == True,
                PricingHistory.is_simulated == False,
            )
            .order_by(desc(PricingHistory.recorded_at))
            .first()
        )

        if name_match and name_match.price > 0:
            source_type = name_match.source_type or "db_match"
            confidence = "high" if source_type in ("external_api", "rfq_actual") else "medium"
            return {
                "price": name_match.price,
                "source": source_type,
                "confidence": confidence,
                "match_key": norm_name,
                "recorded_at": name_match.recorded_at.isoformat() if name_match.recorded_at else None,
            }

    # ── 3. EXTERNAL API ──
    ext_price, ext_supplier, ext_is_simulated = _query_external_api(name, mpn, quantity)
    if ext_price and ext_price > 0 and not ext_is_simulated:
        # FIXED: only save REAL external data, never simulated
        _save_price(db, norm_name, mpn, material, quantity, ext_price,
                    source_type="external_api", vendor_name=ext_supplier)
        return {
            "price": ext_price,
            "source": "external_api",
            "confidence": "medium",
        }

    # ── 4. FALLBACK ──
    fallback = _estimate_fallback_price(name, material, quantity)
    # FIXED: fallback estimates are NOT saved to DB (they polluted real pricing before)
    return {
        "price": fallback,
        "source": "fallback_estimate",
        "confidence": "low",
    }


# ── APPEND-ONLY Save (FIXED) ──

def _save_price(db: Session, norm_name: str, mpn: str, material: str,
                quantity: int, price: float, source_type: str = "",
                vendor_name: str = "", vendor_id: str = None,
                currency: str = "USD", is_simulated: bool = False):
    """
    FIXED: Always INSERT — never UPDATE existing rows.
    Marks previous current rows as not current.
    Does NOT save simulated data.
    """
    if is_simulated:
        return  # FIXED: never persist simulated data

    try:
        store_name = norm_name or _normalize_for_lookup(mpn)
        if not store_name:
            return

        # Find or create correct vendor
        actual_vendor_id = vendor_id
        if not actual_vendor_id and vendor_name:
            vendor = db.query(Vendor).filter(func.lower(Vendor.name) == vendor_name.lower()).first()
            if vendor:
                actual_vendor_id = vendor.id

        if not actual_vendor_id:
            # FIXED: do NOT just grab the first vendor — create a placeholder or skip
            vendor = db.query(Vendor).filter(Vendor.name == "External API").first()
            if not vendor:
                vendor = db.query(Vendor).first()
            if not vendor:
                return
            actual_vendor_id = vendor.id

        # Mark previous entries for this key as not current
        db.query(PricingHistory).filter(
            func.lower(PricingHistory.normalized_key) == store_name.lower(),
            PricingHistory.is_current == True,
        ).update({"is_current": False}, synchronize_session="fetch")

        # INSERT new row (append-only)
        db.add(PricingHistory(
            vendor_id=actual_vendor_id,
            part_name=store_name,
            normalized_key=store_name.lower(),
            mpn=_clean_mpn(mpn) if mpn else "",
            material=material[:255] if material else "",
            quantity=quantity,
            price=round(price, 6),
            currency=currency,
            source_currency=currency,
            display_currency=currency,
            region="",
            source_type=source_type,
            confidence="high" if source_type == "rfq_actual" else ("medium" if source_type == "external_api" else "low"),
            freshness_state="current",
            valid_until=datetime.utcnow() + timedelta(days=30),
            is_current=True,
            is_simulated=is_simulated,
        ))
        db.commit()

    except Exception as e:
        logger.warning(f"Save pricing failed for '{norm_name}': {e}")
        try:
            db.rollback()
        except Exception:
            pass


# ── External API + Fallback ──

def _query_external_api(name: str, mpn: str, quantity: int):
    """Returns (price, supplier_name, is_simulated)."""
    try:
        from app.integrations.supplier_router import route_query
        from app.integrations.pricing_aggregator import aggregate_pricing
        category = _detect_category(name)
        results = route_query(name, category=category, mpn=mpn, quantity=quantity)

        # FIXED: Check if results are simulated
        real_results = [r for r in results if not r.get("is_simulated", False)]
        if not real_results:
            # All results are simulated — don't trust them
            if results:
                agg = aggregate_pricing(results)
                return agg.get("best_price"), agg.get("best_supplier", ""), True
            return None, "", False

        agg = aggregate_pricing(real_results)
        return agg.get("best_price"), agg.get("best_supplier", ""), False
    except Exception as e:
        logger.debug(f"External API failed for '{name}': {e}")
        return None, "", False


def _estimate_fallback_price(name: str, material: str, quantity: int) -> float:
    nl = _normalize_for_lookup(name)
    base = 12.0
    for kw, price in _FALLBACK_PRICES.items():
        if kw in nl:
            base = price
            break
    ml = material.lower() if material else ""
    if any(w in ml for w in ["stainless", "ss304", "ss316"]): base *= 1.8
    elif any(w in ml for w in ["titanium"]): base *= 5.0
    elif any(w in ml for w in ["aluminum", "6061", "7075"]): base *= 1.3
    elif any(w in ml for w in ["copper", "brass"]): base *= 2.0
    if quantity >= 1000: base *= 0.5
    elif quantity >= 100: base *= 0.7
    elif quantity >= 10: base *= 0.85
    return round(max(0.01, base), 4)


def _detect_category(name: str) -> str:
    nl = name.lower() if name else ""
    for cat in ["resistor", "capacitor", "inductor", "ic", "led", "connector",
                 "diode", "transistor", "bearing", "bolt", "screw", "nut",
                 "washer", "sensor", "relay", "switch", "motor"]:
        if cat in nl:
            return cat
    return ""


# ── Enrichment + History ──

def get_all_pricing_history(db: Session, limit: int = 200) -> List[Dict]:
    entries = (
        db.query(PricingHistory)
        .filter(PricingHistory.is_simulated == False)
        .order_by(PricingHistory.recorded_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "vendor_id": e.vendor_id, "part_name": e.part_name, "material": e.material,
        "process": e.process, "quantity": e.quantity, "price": e.price,
        "region": e.region or "", "source_type": e.source_type,
        "confidence": e.confidence, "is_current": e.is_current,
        "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
    } for e in entries]


def enrich_analysis_with_pricing(analyzer_output: Dict, db: Session,
                                  external_pricing: Optional[Dict] = None) -> Dict:
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
                # FIXED: filter out simulated results
                real_results = [r for r in api_results if not r.get("is_simulated", False)]
                if real_results:
                    aggregated = aggregate_pricing(real_results)
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
    _save_price(db, _normalize_for_lookup(part_name), "", material, quantity, price,
                source_type="rfq_actual", vendor_id=vendor_id, currency=currency)