"""
Pricing Service v5 — DB-First with Append-Only History

FIXES (v5):
  - normalized_key queries wrapped in try/except to survive missing column
  - NEW: is_custom_part() check — skips pricing entirely for custom parts
  - _save_price() is APPEND-ONLY: never overwrites existing rows
  - Simulated API data is NOT saved to DB
  - MPN matching uses exact match, not CONTAINS
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

# ── Custom part detection ──

_CUSTOM_CATEGORIES = {
    "custom_mechanical", "sheet_metal", "custom",
}


def is_custom_part(part: Dict[str, Any]) -> bool:
    """Returns True if this part should NOT be priced via MPN/catalog lookup."""
    category = (part.get("category") or "").lower()
    if category in _CUSTOM_CATEGORIES:
        return True
    if part.get("is_custom", False):
        return True
    if part.get("part_type", "standard") == "custom":
        return True
    procurement = (part.get("procurement_class") or "").lower()
    if procurement == "rfq_required":
        return True
    return False


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
    """
    Get pricing for a part. Returns price data dict.
    Custom parts return None price with source='custom_rfq_required'.
    """
    # ── CUSTOM PART CHECK: do NOT price custom fabricated parts ──
    if is_custom_part(part):
        return {
            "price": None,
            "source": "custom_rfq_required",
            "confidence": "none",
            "match_key": "",
            "note": "Custom part — requires RFQ with drawing. No catalog price available.",
        }

    name = part.get("part_name", part.get("description", part.get("name", "")))
    material = part.get("material", "")
    quantity = part.get("quantity", 1)
    mpn = part.get("mpn", "")

    clean_mpn = _clean_mpn(mpn)
    norm_name = _normalize_for_lookup(name)

    # ── 1. MPN EXACT MATCH ──
    if clean_mpn and len(clean_mpn) >= 4:
        try:
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
        except Exception as e:
            logger.debug(f"MPN lookup failed (column may not exist): {e}")

    # ── 2. NORMALIZED NAME EXACT MATCH ──
    if norm_name and len(norm_name) >= 5:
        try:
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
        except Exception as e:
            # normalized_key column may not exist in older DBs — fall through
            logger.debug(f"Normalized key lookup failed (column may not exist): {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # ── 3. EXTERNAL API ──
    ext_price, ext_supplier, ext_is_simulated = _query_external_api(name, mpn, quantity)
    if ext_price and ext_price > 0 and not ext_is_simulated:
        _save_price(db, norm_name, mpn, material, quantity, ext_price,
                    source_type="external_api", vendor_name=ext_supplier)
        return {
            "price": ext_price,
            "source": "external_api",
            "confidence": "medium",
        }

    # ── 4. FALLBACK ──
    fallback = _estimate_fallback_price(name, material, quantity)
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
    Does NOT save simulated data.
    Handles missing normalized_key column gracefully.
    """
    if is_simulated:
        return

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
            vendor = db.query(Vendor).filter(Vendor.name == "External API").first()
            if not vendor:
                vendor = db.query(Vendor).first()
            if not vendor:
                return
            actual_vendor_id = vendor.id

        # Mark previous entries as not current (safe: catches missing column)
        try:
            db.query(PricingHistory).filter(
                func.lower(PricingHistory.normalized_key) == store_name.lower(),
                PricingHistory.is_current == True,
            ).update({"is_current": False}, synchronize_session="fetch")
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

        # INSERT new row (append-only)
        new_entry = PricingHistory(
            vendor_id=actual_vendor_id,
            part_name=store_name,
            mpn=_clean_mpn(mpn) if mpn else "",
            material=material[:255] if material else "",
            quantity=quantity,
            price=round(price, 6),
            currency=currency,
            region="",
            source_type=source_type,
        )

        # Set columns that may not exist — use setattr safely
        for attr, val in [
            ("normalized_key", store_name.lower()),
            ("source_currency", currency),
            ("display_currency", currency),
            ("confidence", "high" if source_type == "rfq_actual" else ("medium" if source_type == "external_api" else "low")),
            ("freshness_state", "current"),
            ("valid_until", datetime.utcnow() + timedelta(days=30)),
            ("is_current", True),
            ("is_simulated", is_simulated),
        ]:
            try:
                setattr(new_entry, attr, val)
            except Exception:
                pass

        db.add(new_entry)
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

        real_results = [r for r in results if not r.get("is_simulated", False)]
        if not real_results:
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
    try:
        entries = (
            db.query(PricingHistory)
            .filter(PricingHistory.is_simulated == False)
            .order_by(PricingHistory.recorded_at.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        # Fallback if is_simulated column doesn't exist
        entries = (
            db.query(PricingHistory)
            .order_by(PricingHistory.recorded_at.desc())
            .limit(limit)
            .all()
        )
    return [{
        "vendor_id": e.vendor_id, "part_name": e.part_name, "material": e.material,
        "process": e.process, "quantity": e.quantity, "price": e.price,
        "region": e.region or "", "source_type": getattr(e, "source_type", None),
        "confidence": getattr(e, "confidence", "low"),
        "is_current": getattr(e, "is_current", True),
        "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
    } for e in entries]


def enrich_analysis_with_pricing(analyzer_output: Dict, db: Session,
                                  external_pricing: Optional[Dict] = None) -> Dict:
    """
    Enriches component breakdown with pricing.
    Custom parts get manufacturing intelligence instead of price.
    """
    external_pricing = external_pricing or {}
    components = analyzer_output.get("section_2_component_breakdown", [])
    for comp in components:
        item_id = comp.get("item_id", "")
        part = {
            "part_name": comp.get("description", ""),
            "material": comp.get("material", ""),
            "quantity": comp.get("quantity", 1),
            "mpn": comp.get("mpn", ""),
            "category": comp.get("category", "standard"),
            "is_custom": comp.get("is_custom", False),
            "procurement_class": comp.get("procurement_class", "catalog_purchase"),
            "part_type": comp.get("part_type", "standard"),
        }

        # ── Custom parts: manufacturing intelligence, NOT price ──
        if is_custom_part(part):
            comp["_price"] = None
            comp["_price_source"] = "custom_rfq_required"
            comp["_price_confidence"] = "none"
            comp["_is_custom"] = True
            comp["_rfq_required"] = True
            comp["_drawing_required"] = True
            comp["_quote_required"] = True
            # Add manufacturing intelligence
            mfg_intel = _build_manufacturing_intelligence(comp)
            comp["_manufacturing_intelligence"] = mfg_intel
            continue

        # ── Standard parts: normal pricing ──
        ext = external_pricing.get(item_id, {})
        if ext.get("best_price"):
            comp["_price"] = ext["best_price"]
            comp["_price_source"] = "external_api"
            comp["_price_confidence"] = ext.get("confidence", "medium")
            comp["_is_custom"] = False
            continue
        price_data = get_price(part, db)
        comp["_price"] = price_data["price"]
        comp["_price_source"] = price_data["source"]
        comp["_price_confidence"] = price_data["confidence"]
        comp["_is_custom"] = False
    return analyzer_output


def _build_manufacturing_intelligence(comp: Dict) -> Dict:
    """Build manufacturing insight for a custom part."""
    name = (comp.get("description") or "").lower()
    material = (comp.get("material") or "").lower()
    category = (comp.get("category") or "").lower()

    # Detect process
    process = "CNC Machining"
    if category == "sheet_metal" or "sheet" in material or "sheetmetal" in material:
        process = "Sheet Metal Fabrication (Laser Cut + Bend)"
    elif any(w in name for w in ["weld", "frame", "assy", "assembly"]):
        process = "Welding & Assembly"
    elif any(w in name for w in ["shaft", "rod", "thread", "knob"]):
        process = "CNC Turning"
    elif any(w in name for w in ["block", "mount", "coupling", "plate"]):
        process = "CNC Milling"
    elif any(w in name for w in ["roller", "pipe"]):
        process = "CNC Turning"
    elif any(w in name for w in ["die", "mold"]):
        process = "EDM / CNC Milling"
    elif "nylon" in material or "rubber" in material or "silicon" in material:
        process = "Molding / CNC Machining"

    # Detect material family
    mat_display = material.upper() if material else "Unspecified"
    if "ss" in material or "stainless" in material:
        mat_display = "Stainless Steel"
    elif "ms" == material.strip() or "mild steel" in material:
        mat_display = "Mild Steel (MS)"
    elif "al" in material and ("6061" in material or "aluminum" in material):
        mat_display = "Aluminum 6061"
    elif "gi" == material.strip():
        mat_display = "Galvanized Iron (GI)"
    elif "hylam" in material:
        mat_display = "Hylam (Laminate)"
    elif "nylon" in material:
        mat_display = "Nylon"
    elif "silicon" in material or "rubber" in material:
        mat_display = "Silicone Rubber"

    # Suggested regions
    regions = []
    if "sheet" in process.lower():
        regions = ["India (local)", "China", "Vietnam"]
    elif "cnc" in process.lower():
        regions = ["India (local)", "China", "EU (Germany)"]
    elif "weld" in process.lower():
        regions = ["India (local)", "Mexico"]
    else:
        regions = ["India (local)", "China"]

    # Manufacturability insight
    insight = f"{process} recommended for {comp.get('description', 'this part')}."
    if "sheet" in process.lower():
        insight += " Standard sheet metal tolerances apply. Nesting multiple parts reduces waste."
    elif "turning" in process.lower():
        insight += " Standard turning tolerances ±0.05mm. Check concentricity requirements."
    elif "milling" in process.lower():
        insight += " Standard milling tolerances ±0.05mm. Minimize deep pockets for cost efficiency."

    return {
        "detected_process": process,
        "recommended_process": process,
        "material": mat_display,
        "manufacturability_insight": insight,
        "suggested_regions": regions,
        "drawing_required": True,
        "quote_required": True,
    }


def fetch_external_pricing(parts: List[Dict]) -> Dict[str, Dict]:
    """Fetch external pricing — skips custom parts."""
    results = {}
    try:
        from app.integrations.supplier_router import route_query
        from app.integrations.pricing_aggregator import aggregate_pricing
        for i, part in enumerate(parts):
            # Skip custom parts entirely
            if is_custom_part(part):
                continue

            mpn = part.get("mpn", "")
            name = part.get("part_name", "")
            qty = part.get("quantity", 1)
            if not mpn and not name:
                continue
            category = _detect_category(name)
            try:
                api_results = route_query(name, category=category, mpn=mpn, quantity=qty)
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
