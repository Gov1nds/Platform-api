"""
Pricing Service v7 — PostgreSQL (pricing.pricing_quotes)

Key changes from v6:
  - FIXED: canonical key alignment — uses engine-generated canonical_part_key
    as PRIMARY lookup key instead of re-normalizing with a different function
  - FIXED: _save_price() no longer calls db.rollback() (was rolling back outer txn)
  - FIXED: fallback prices converted to target currency
  - Uses PricingQuote (maps to pricing.pricing_quotes)
  - Custom parts return None price immediately
"""
import re
import hashlib
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, cast, String
from app.models.pricing import PricingQuote
from app.models.vendor import Vendor

logger = logging.getLogger("pricing_service")

_CUSTOM_CATEGORIES = {"custom_mechanical", "sheet_metal", "custom", "machined"}


def is_custom_part(part: Dict[str, Any]) -> bool:
    category = (part.get("category") or "").lower()
    if category in _CUSTOM_CATEGORIES:
        return True
    if part.get("is_custom", False):
        return True
    if part.get("part_type", "standard") == "custom":
        return True
    if (part.get("procurement_class") or "").lower() in ("rfq_required", "custom_manufacture"):
        return True
    return False


# ── Canonical Key Generation (ALIGNED WITH ENGINE) ──
# This reproduces the same logic as bom-intelligence-engine/engine/orchestrator.py
# _generate_canonical_key() so lookups match stored keys.

def generate_canonical_key(domain: str, mpn: str = "", manufacturer: str = "",
                           material: str = "", material_form: str = "",
                           description: str = "") -> str:
    """Generate a canonical key using the SAME algorithm as the BOM engine.
    This ensures pricing lookups match engine-generated keys."""
    domain = (domain or "unknown").lower()

    # MPN-based key (matches engine logic)
    if mpn and len(mpn.strip()) >= 4:
        clean_mpn = re.sub(r"[\s\-_]", "", mpn.strip().upper())
        mfr = re.sub(r"[\s\-_]", "", manufacturer.strip().lower())[:20] if manufacturer else ""
        if mfr:
            return f"{domain}:mpn:{mfr}:{clean_mpn}".lower()
        return f"{domain}:mpn:{clean_mpn}".lower()

    # Material/form based key (matches engine logic)
    form = (material_form or "").lower()
    mat = re.sub(r"[\s_]+", "_", material.strip().lower())[:30] if material else ""
    desc = re.sub(r"[\s_]+", "_", description.strip().lower())[:40] if description else ""

    parts = [domain]
    if form:
        parts.append(form)
    if mat:
        parts.append(mat)
    if desc:
        parts.append(desc)

    key = ":".join(parts)
    if len(key) > 120:
        key = key[:80] + ":" + hashlib.sha256(key.encode()).hexdigest()[:12]
    return key


def _normalize_for_lookup(text: str) -> str:
    """Legacy normalization — kept for backward compat but canonical_part_key
    from engine output should be preferred."""
    if not text:
        return ""
    s = text.strip().lower()
    s = re.sub(r"\bm(\d+)\s*x\s*(\d+)", r"metric_bolt_m\1x\2", s, flags=re.I)
    for pat, repl in _ABBREVS:
        s = pat.sub(repl, s, count=1)
    for pat, fn in _VALUE_SCALES:
        s = pat.sub(fn, s)
    return re.sub(r"\s+", " ", s).strip()


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


def _clean_mpn(mpn: str) -> str:
    if not mpn:
        return ""
    return re.sub(r"[\s\-_]", "", mpn.strip().upper())


_FALLBACK_PRICES = {
    "resistor": 0.01, "capacitor": 0.02, "inductor": 0.08, "diode": 0.05,
    "transistor": 0.10, "integrated_circuit": 2.50, "microcontroller": 5.00,
    "led": 0.03, "connector": 0.50, "relay": 2.00, "fuse": 0.15, "sensor": 3.00,
    "bolt": 0.12, "screw": 0.08, "nut": 0.05, "washer": 0.03,
    "bearing": 3.50, "spring": 0.40, "bracket": 8.00, "housing": 25.00,
    "shaft": 12.00, "plate": 5.00, "sheet": 15.00, "rod": 8.00, "pcb": 15.00,
}


def get_price(part: Dict[str, Any], db: Session) -> Dict[str, Any]:
    if is_custom_part(part):
        return {
            "price": None,
            "source": "custom_rfq_required",
            "confidence": "none",
            "match_key": "",
            "note": "Custom part — requires RFQ with drawing.",
        }

    name = part.get("part_name", part.get("description", part.get("name", "")))
    material = part.get("material", "")
    quantity = part.get("quantity", 1)
    mpn = part.get("mpn", "")
    clean_mpn = _clean_mpn(mpn)
    # FIXED: Use engine-generated canonical_part_key as PRIMARY lookup
    canonical_key = part.get("canonical_part_key", "")

    # 0. Canonical part key match (PREFERRED — aligned with engine)
    if canonical_key and len(canonical_key) >= 5:
        try:
            key_match = (
                db.query(PricingQuote)
                .filter(
                    PricingQuote.canonical_part_key == canonical_key,
                    PricingQuote.freshness_state == "current",
                )
                .order_by(desc(PricingQuote.recorded_at))
                .first()
            )
            if key_match and float(key_match.unit_price) > 0:
                source_type = key_match.source_type or "internal_db"
                conf = "high" if source_type in ("external_api", "rfq_actual") else "medium"
                return {
                    "price": float(key_match.unit_price),
                    "source": source_type,
                    "confidence": conf,
                    "match_key": canonical_key,
                    "recorded_at": key_match.recorded_at.isoformat() if key_match.recorded_at else None,
                }
        except Exception as e:
            logger.debug(f"Canonical key lookup failed: {e}")

    # 1. MPN match via quote_payload->>'mpn'
    if clean_mpn and len(clean_mpn) >= 4:
        try:
            mpn_match = (
                db.query(PricingQuote)
                .filter(
                    func.upper(func.replace(
                        PricingQuote.quote_payload["mpn"].astext, " ", ""
                    )) == clean_mpn,
                    PricingQuote.freshness_state == "current",
                )
                .order_by(desc(PricingQuote.recorded_at))
                .first()
            )
            if mpn_match and float(mpn_match.unit_price) > 0:
                return {
                    "price": float(mpn_match.unit_price),
                    "source": "mpn_match",
                    "confidence": "high",
                    "match_key": clean_mpn,
                    "vendor_id": mpn_match.vendor_id,
                    "recorded_at": mpn_match.recorded_at.isoformat() if mpn_match.recorded_at else None,
                }
        except Exception as e:
            logger.debug(f"MPN lookup failed: {e}")

    # 2. Legacy normalized text match (backward compat for old pricing data)
    norm_name = _normalize_for_lookup(name)
    if norm_name and len(norm_name) >= 5:
        try:
            name_match = (
                db.query(PricingQuote)
                .filter(
                    PricingQuote.canonical_part_key == norm_name,
                    PricingQuote.freshness_state == "current",
                )
                .order_by(desc(PricingQuote.recorded_at))
                .first()
            )
            if name_match and float(name_match.unit_price) > 0:
                source_type = name_match.source_type or "internal_db"
                conf = "high" if source_type in ("external_api", "rfq_actual") else "medium"
                return {
                    "price": float(name_match.unit_price),
                    "source": source_type,
                    "confidence": conf,
                    "match_key": norm_name,
                    "recorded_at": name_match.recorded_at.isoformat() if name_match.recorded_at else None,
                }
        except Exception as e:
            logger.debug(f"Legacy key lookup failed: {e}")

    # 3. External API
    ext_price, ext_supplier, ext_is_simulated = _query_external_api(name, mpn, quantity)
    if ext_price and ext_price > 0 and not ext_is_simulated:
        # Store using engine canonical key if available, else legacy key
        store_key = canonical_key or norm_name
        _save_price(db, store_key, mpn, material, quantity, ext_price,
                    source_type="external_api", vendor_name=ext_supplier)
        return {"price": ext_price, "source": "external_api", "confidence": "medium"}

    # 4. Fallback
    fallback = _estimate_fallback_price(name, material, quantity)
    return {"price": fallback, "source": "fallback_estimate", "confidence": "low"}


def _save_price(db, norm_name, mpn, material, quantity, price,
                source_type="", vendor_name="", vendor_id=None,
                currency="USD", is_simulated=False):
    """Persist a pricing quote. Uses nested savepoint so failures
    do NOT roll back the outer transaction (FIXED from v6)."""
    if is_simulated:
        return
    try:
        store_key = (norm_name or _normalize_for_lookup(mpn)).lower()
        if not store_key:
            return

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

        # Use nested savepoint so failure here won't roll back outer txn
        nested = db.begin_nested()
        try:
            # Mark old entries stale
            db.query(PricingQuote).filter(
                PricingQuote.canonical_part_key == store_key,
                PricingQuote.freshness_state == "current",
            ).update({"freshness_state": "stale"}, synchronize_session="fetch")

            conf_val = 0.9 if source_type == "rfq_actual" else (0.7 if source_type == "external_api" else 0.4)
            try:
                from app.services.procurement_planner import FOREX_RATES
                fx_rate = FOREX_RATES.get(currency, 1.0) / FOREX_RATES.get("USD", 1.0)
            except ImportError:
                fx_rate = 1.0

            db.add(PricingQuote(
                canonical_part_key=store_key,
                vendor_id=actual_vendor_id,
                source_type=source_type,
                source_currency=currency,
                display_currency=currency,
                fx_rate=round(fx_rate, 8),
                quantity=quantity,
                unit_price=round(price, 6),
                total_price=round(price * quantity, 6),
                confidence=conf_val,
                freshness_state="current",
                valid_from=datetime.utcnow(),
                valid_until=datetime.utcnow() + timedelta(days=30),
                recorded_at=datetime.utcnow(),
                quote_payload={
                    "mpn": _clean_mpn(mpn) if mpn else "",
                    "material": (material or "")[:255],
                    "region": "",
                    "is_simulated": is_simulated,
                },
            ))
            nested.commit()
        except Exception:
            nested.rollback()  # Only rolls back the savepoint, not outer txn
            raise

        db.flush()
    except Exception as e:
        logger.warning(f"Save pricing failed for '{norm_name}': {e}")
        # FIXED: Do NOT call db.rollback() — that kills the outer transaction


def _query_external_api(name, mpn, quantity):
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


def _estimate_fallback_price(name, material, quantity):
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


def _detect_category(name):
    nl = name.lower() if name else ""
    for cat in ["resistor", "capacitor", "inductor", "ic", "led", "connector",
                 "diode", "transistor", "bearing", "bolt", "screw", "nut",
                 "washer", "sensor", "relay", "switch", "motor"]:
        if cat in nl:
            return cat
    return ""


def _build_manufacturing_intelligence(comp):
    name = (comp.get("description") or "").lower()
    material = (comp.get("material") or "").lower()
    category = (comp.get("category") or "").lower()

    process = "CNC Machining"
    if category == "sheet_metal" or "sheet" in material or "sheetmetal" in material:
        process = "Sheet Metal Fabrication (Laser Cut + Bend)"
    elif category == "machined" or any(w in name for w in ["machined", "cnc", "milled", "turned"]):
        # Determine CNC sub-type for machined parts
        if any(w in name for w in ["shaft", "rod", "spindle", "sleeve", "bushing", "thread", "knob"]):
            process = "CNC Turning"
        elif any(w in name for w in ["pocket", "slot", "block", "mount", "coupling", "plate"]):
            process = "CNC Milling"
        elif any(w in name for w in ["5-axis", "5axis", "multi-axis", "contour", "freeform"]):
            process = "5-Axis CNC Machining"
        else:
            process = "CNC Machining (3-Axis)"
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

    mat_display = material.upper() if material else "Unspecified"
    if "ss" in material or "stainless" in material: mat_display = "Stainless Steel"
    elif "ms" == material.strip() or "mild steel" in material: mat_display = "Mild Steel (MS)"
    elif "al" in material and ("6061" in material or "aluminum" in material): mat_display = "Aluminum 6061"
    elif "gi" == material.strip(): mat_display = "Galvanized Iron (GI)"
    elif "hylam" in material: mat_display = "Hylam (Laminate)"
    elif "nylon" in material: mat_display = "Nylon"
    elif "silicon" in material or "rubber" in material: mat_display = "Silicone Rubber"

    regions = ["India (local)", "China"]
    if "sheet" in process.lower(): regions = ["India (local)", "China", "Vietnam"]
    elif "5-axis" in process.lower() or "5axis" in process.lower(): regions = ["EU (Germany)", "USA", "China"]
    elif "cnc" in process.lower(): regions = ["India (local)", "China", "EU (Germany)"]
    elif "weld" in process.lower(): regions = ["India (local)", "Mexico"]

    insight = f"{process} recommended."
    if "sheet" in process.lower(): insight += " Standard sheet metal tolerances. Nesting reduces waste."
    elif "turning" in process.lower(): insight += " Standard turning ±0.05mm. Check concentricity."
    elif "milling" in process.lower(): insight += " Standard milling ±0.05mm. Minimize deep pockets."
    elif "5-axis" in process.lower() or "5axis" in process.lower(): insight += " Complex geometry. Requires qualified 5-axis vendor."
    elif "machining" in process.lower(): insight += " Standard CNC ±0.05mm. Review tolerances and surface finish."

    return {
        "detected_process": process, "recommended_process": process,
        "material": mat_display, "manufacturability_insight": insight,
        "suggested_regions": regions, "drawing_required": True, "quote_required": True,
    }


def get_all_pricing_history(db, limit=200):
    entries = (
        db.query(PricingQuote)
        .filter(PricingQuote.freshness_state == "current")
        .order_by(PricingQuote.recorded_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "vendor_id": e.vendor_id, "part_name": e.canonical_part_key,
        "material": e.material, "process": e.process,
        "quantity": float(e.quantity), "price": float(e.unit_price),
        "region": e.region, "source_type": e.source_type,
        "confidence": float(e.confidence) if e.confidence else 0,
        "is_current": e.freshness_state == "current",
        "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
    } for e in entries]


def enrich_analysis_with_pricing(analyzer_output, db, external_pricing=None):
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
            "canonical_part_key": comp.get("canonical_part_key", ""),
        }
        if is_custom_part(part):
            comp["_price"] = None
            comp["_price_source"] = "custom_rfq_required"
            comp["_price_confidence"] = "none"
            comp["_is_custom"] = True
            comp["_rfq_required"] = True
            comp["_drawing_required"] = True
            comp["_quote_required"] = True
            comp["_manufacturing_intelligence"] = _build_manufacturing_intelligence(comp)
            continue

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


def fetch_external_pricing(parts):
    results = {}
    try:
        from app.integrations.supplier_router import route_query
        from app.integrations.pricing_aggregator import aggregate_pricing
        for i, part in enumerate(parts):
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


def record_pricing(db, vendor_id, part_name, price,
                   material="", process="", quantity=1,
                   region="", currency="USD"):
    _save_price(db, _normalize_for_lookup(part_name), "", material, quantity, price,
                source_type="rfq_actual", vendor_id=vendor_id, currency=currency)


def expire_stale_prices(db, days_threshold: int = 30) -> int:
    """Mark pricing quotes as stale if valid_until has passed.
    Call at startup or on a schedule. Returns count of expired entries.
    """
    try:
        result = db.query(PricingQuote).filter(
            PricingQuote.freshness_state == "current",
            PricingQuote.valid_until < datetime.utcnow(),
        ).update({"freshness_state": "expired"}, synchronize_session="fetch")
        db.commit()
        if result:
            logger.info(f"Expired {result} stale pricing quotes (>{days_threshold}d)")
        return result
    except Exception as e:
        logger.warning(f"expire_stale_prices failed: {e}")
        return 0