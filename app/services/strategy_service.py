"""
Strategy Service FINAL — Global Procurement Optimization Engine

IMPLEMENTS ALL 10 REQUIREMENTS:
  1. Global optimization (evaluate BOM-wide strategy combinations)
  2. Supplier capability filtering (process → vendor match)
  3. MOQ & batch logic (penalize where MOQ > required qty)
  4. Advanced logistics (distance-based, weight-scaled, consolidation)
  5. Urgency mode (priority="cost" or "speed")
  6. True savings (naive_local vs optimized_global)
  7. Advanced explanation (%, logistics, quantity, region)
  8. Risk model (supplier_memory + region risk + delivery variance)
  9. Learning integration (supplier_memory adjusts uncertainty)
  10. Procurement plan output with region_distribution

Uses get_price(part, db) from pricing_service for DB-first pricing.
"""
import logging
import math
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from sqlalchemy.orm import Session

logger = logging.getLogger("strategy_service")

# ══════════════════════════════════════════════════════════
# REGION PROFILES (with distance model)
# ══════════════════════════════════════════════════════════

REGION_PROFILES = {
    "India": {
        "base_cost_mult": 0.35, "labor_rate_hr": 12, "lead_days_base": 18,
        "logistics_per_kg": 3.5, "tariff_pct": 0.05, "risk_base": 0.15,
        "quality_score": 0.78, "moq_threshold": 50,
        "distance_km": {"India": 500, "USA": 14000, "EU (Germany)": 7000, "China": 5000, "Local": 500},
        "process_fit": {"sheet_metal": 0.95, "CNC": 0.85, "fasteners": 0.95, "welding": 0.90,
                        "injection_molding": 0.65, "die_casting": 0.60, "PCB": 0.70, "electronics": 0.75},
        "material_fit": {"stainless_steel": 0.90, "carbon_steel": 0.95, "aluminum": 0.85, "plastic": 0.65},
        "capabilities": ["CNC", "sheet_metal", "fasteners", "welding", "assembly", "electronics"],
    },
    "China": {
        "base_cost_mult": 0.40, "labor_rate_hr": 15, "lead_days_base": 22,
        "logistics_per_kg": 3.0, "tariff_pct": 0.08, "risk_base": 0.18,
        "quality_score": 0.82, "moq_threshold": 100,
        "distance_km": {"India": 5000, "USA": 12000, "EU (Germany)": 8000, "China": 300, "Local": 12000},
        "process_fit": {"injection_molding": 0.98, "die_casting": 0.95, "PCB": 0.95, "electronics": 0.92,
                        "stamping": 0.90, "CNC": 0.80, "sheet_metal": 0.82, "fasteners": 0.85},
        "material_fit": {"plastic": 0.95, "aluminum": 0.85, "stainless_steel": 0.80, "carbon_steel": 0.85},
        "capabilities": ["injection_molding", "die_casting", "PCB", "electronics", "stamping", "CNC", "sheet_metal"],
    },
    "Vietnam": {
        "base_cost_mult": 0.38, "labor_rate_hr": 10, "lead_days_base": 24,
        "logistics_per_kg": 4.0, "tariff_pct": 0.04, "risk_base": 0.20,
        "quality_score": 0.72, "moq_threshold": 100,
        "distance_km": {"India": 4000, "USA": 14000, "EU (Germany)": 9000, "China": 2000, "Local": 14000},
        "process_fit": {"assembly": 0.90, "wiring": 0.88, "CNC": 0.60, "sheet_metal": 0.65, "electronics": 0.72},
        "material_fit": {"plastic": 0.70, "carbon_steel": 0.65, "aluminum": 0.60},
        "capabilities": ["assembly", "wiring", "CNC", "electronics"],
    },
    "Mexico": {
        "base_cost_mult": 0.55, "labor_rate_hr": 18, "lead_days_base": 12,
        "logistics_per_kg": 2.0, "tariff_pct": 0.02, "risk_base": 0.12,
        "quality_score": 0.80, "moq_threshold": 25,
        "distance_km": {"India": 16000, "USA": 2000, "EU (Germany)": 9000, "China": 12000, "Local": 2000},
        "process_fit": {"sheet_metal": 0.85, "stamping": 0.88, "assembly": 0.82, "CNC": 0.75},
        "material_fit": {"carbon_steel": 0.85, "stainless_steel": 0.78, "aluminum": 0.80},
        "capabilities": ["sheet_metal", "stamping", "assembly", "CNC"],
    },
    "EU (Germany)": {
        "base_cost_mult": 0.90, "labor_rate_hr": 55, "lead_days_base": 10,
        "logistics_per_kg": 1.5, "tariff_pct": 0.03, "risk_base": 0.05,
        "quality_score": 0.95, "moq_threshold": 5,
        "distance_km": {"India": 7000, "USA": 8000, "EU (Germany)": 300, "China": 8000, "Local": 8000},
        "process_fit": {"CNC": 0.98, "precision_CNC": 1.0, "grinding": 0.95, "5_axis": 0.98,
                        "sheet_metal": 0.80, "injection_molding": 0.75},
        "material_fit": {"stainless_steel": 0.95, "aluminum": 0.95, "titanium": 0.90, "carbon_steel": 0.90},
        "capabilities": ["CNC", "precision_CNC", "grinding", "5_axis", "sheet_metal", "injection_molding"],
    },
    "USA": {
        "base_cost_mult": 1.00, "labor_rate_hr": 65, "lead_days_base": 8,
        "logistics_per_kg": 1.0, "tariff_pct": 0.00, "risk_base": 0.03,
        "quality_score": 0.93, "moq_threshold": 5,
        "distance_km": {"India": 14000, "USA": 300, "EU (Germany)": 8000, "China": 12000, "Local": 300},
        "process_fit": {"CNC": 0.95, "precision_CNC": 0.95, "3d_printing": 0.98, "PCB": 0.85,
                        "sheet_metal": 0.80, "electronics": 0.88},
        "material_fit": {"aluminum": 0.95, "stainless_steel": 0.90, "titanium": 0.92},
        "capabilities": ["CNC", "precision_CNC", "3d_printing", "PCB", "sheet_metal", "electronics"],
    },
    "Local": {
        "base_cost_mult": 0.95, "labor_rate_hr": 50, "lead_days_base": 7,
        "logistics_per_kg": 0.5, "tariff_pct": 0.00, "risk_base": 0.05,
        "quality_score": 0.88, "moq_threshold": 1,
        "distance_km": {"India": 500, "USA": 300, "EU (Germany)": 300, "China": 300, "Local": 50},
        "process_fit": {"CNC": 0.80, "sheet_metal": 0.80, "3d_printing": 0.85, "assembly": 0.75,
                        "fasteners": 0.70, "electronics": 0.70},
        "material_fit": {"aluminum": 0.80, "stainless_steel": 0.80, "carbon_steel": 0.80, "plastic": 0.75},
        "capabilities": ["CNC", "sheet_metal", "3d_printing", "assembly"],
    },
}

TRANSPORT_MODES = {
    "air": {"cost_per_kg_per_km": 0.0004, "days_per_1000km": 0.3, "reliability": 0.95, "base_cost": 80},
    "sea": {"cost_per_kg_per_km": 0.00005, "days_per_1000km": 2.5, "reliability": 0.85, "base_cost": 150},
    "road": {"cost_per_kg_per_km": 0.0002, "days_per_1000km": 1.0, "reliability": 0.90, "base_cost": 30},
    "rail": {"cost_per_kg_per_km": 0.0001, "days_per_1000km": 1.5, "reliability": 0.88, "base_cost": 60},
}

QTY_OVERRIDE = 500
_PROCESS_KW = {
    "CNC": ["cnc", "machined", "milled", "turned"], "precision_CNC": ["precision", "5-axis", "tight tolerance"],
    "sheet_metal": ["sheet metal", "laser cut", "bend", "press brake"],
    "injection_molding": ["injection", "molded"], "die_casting": ["die cast", "casting"],
    "PCB": ["pcb", "circuit board"], "electronics": ["resistor", "capacitor", "ic", "led", "diode", "transistor", "connector"],
    "fasteners": ["bolt", "screw", "nut", "washer", "rivet"], "welding": ["welded", "weld"],
    "3d_printing": ["3d print", "additive", "sls", "sla"],
}
_WEIGHT_EST = {"standard": 0.01, "custom": 0.3, "raw_material": 0.5,
               "bolt": 0.02, "screw": 0.01, "nut": 0.008, "bearing": 0.15, "bracket": 0.3, "housing": 0.8}


# ══════════════════════════════════════════════════════════
# CORE COST FUNCTIONS
# ══════════════════════════════════════════════════════════

def calculate_logistics_cost(weight_kg: float, region: str, delivery_country: str,
                              urgency: str = "cost") -> Dict:
    """Distance-based, weight-scaled logistics with consolidation discount."""
    profile = REGION_PROFILES.get(region, REGION_PROFILES["Local"])
    dist = profile.get("distance_km", {}).get(delivery_country, profile.get("distance_km", {}).get("Local", 5000))

    # Select mode based on urgency + distance + weight
    if region in (delivery_country, "Local") or dist < 1000:
        mode = "road"
    elif urgency == "speed":
        mode = "air"
    elif weight_kg > 500 and dist > 5000:
        mode = "sea"
    elif dist > 8000:
        mode = "sea"
    else:
        mode = "air" if weight_kg < 20 else "rail"

    t = TRANSPORT_MODES[mode]
    freight = t["base_cost"] + max(weight_kg, 0.5) * dist * t["cost_per_kg_per_km"]
    # Consolidation discount: heavier = cheaper per kg
    if weight_kg > 100: freight *= 0.85
    elif weight_kg > 50: freight *= 0.92

    handling = max(15, freight * 0.06)
    insurance = freight * 0.012
    customs = 50 if region not in ("Local", delivery_country) else 0
    transit_days = max(1, round(dist / 1000 * t["days_per_1000km"]))
    total = freight + handling + insurance + customs

    return {"transport_mode": mode, "distance_km": dist, "freight_cost": round(freight, 2),
            "handling": round(handling, 2), "insurance": round(insurance, 2),
            "customs_clearance": round(customs, 2), "total_logistics": round(total, 2),
            "transit_days": transit_days, "reliability": t["reliability"]}


def calculate_tariff_cost(mfg_cost: float, region: str, delivery_country: str) -> Dict:
    profile = REGION_PROFILES.get(region, REGION_PROFILES["Local"])
    rate = 0.0 if region in (delivery_country, "Local") else profile["tariff_pct"]
    amt = mfg_cost * rate
    cp = 25 if rate > 0 else 0
    cc = 15 if rate > 0 else 0
    return {"tariff_rate": rate, "tariff_amount": round(amt, 2),
            "customs_processing": cp, "compliance_cost": cc,
            "total_tariff": round(amt + cp + cc, 2)}


def calculate_risk(region: str, vendor_memory: Optional[Dict] = None,
                   complexity: str = "medium") -> Dict:
    """Risk model using supplier_memory + region + complexity."""
    profile = REGION_PROFILES.get(region, REGION_PROFILES["Local"])
    base_risk = profile["risk_base"]

    # Vendor reliability from memory
    vendor_risk = 0.30  # unknown
    delivery_variance = 5.0  # days
    if vendor_memory:
        n = vendor_memory.get("total_orders", 0)
        perf = vendor_memory.get("performance_score", 0.5)
        vendor_risk = max(0.03, 0.40 - perf * 0.35 - min(n, 20) * 0.005)
        delivery_variance = max(1, 7 - perf * 5)

    cm = {"low": 0.8, "medium": 1.0, "high": 1.3}.get(complexity, 1.0)
    total_uncertainty = min(0.50, (base_risk + vendor_risk) * cm)

    return {"uncertainty": round(total_uncertainty, 4),
            "confidence": round(max(0, 1 - total_uncertainty * 2), 4),
            "region_risk": round(base_risk, 4), "vendor_risk": round(vendor_risk, 4),
            "delivery_variance_days": round(delivery_variance, 1),
            "is_new_vendor": vendor_memory is None or vendor_memory.get("total_orders", 0) == 0}


def calculate_cost_range(base: float, uncertainty: float) -> Dict:
    lo, hi = base * (1 - uncertainty), base * (1 + uncertainty)
    return {"low": round(lo, 2), "high": round(hi, 2), "average": round((lo + hi) / 2, 2)}


# ══════════════════════════════════════════════════════════
# DETECTION HELPERS
# ══════════════════════════════════════════════════════════

def _detect_process(text):
    tl = text.lower()
    for proc, kws in _PROCESS_KW.items():
        if any(k in tl for k in kws): return proc
    return "CNC"

def _detect_material(text):
    tl = text.lower()
    if any(w in tl for w in ["ss304", "ss316", "stainless"]): return "stainless_steel"
    if any(w in tl for w in ["aluminum", "6061", "7075"]): return "aluminum"
    if any(w in tl for w in ["nylon", "abs", "plastic", "pom"]): return "plastic"
    if any(w in tl for w in ["titanium"]): return "titanium"
    return "carbon_steel"

def _est_weight(name, category, quantity):
    for k, w in _WEIGHT_EST.items():
        if k in name.lower(): return w * quantity
    return _WEIGHT_EST.get(category, 0.05) * quantity

def _capability_match(region, process):
    """Check if region can handle this process."""
    caps = REGION_PROFILES.get(region, {}).get("capabilities", [])
    return process in caps or any(process.lower() in c.lower() for c in caps)

def _moq_penalty(region, quantity):
    """Returns 0-1 penalty. 0 = no penalty, 1 = severe."""
    moq = REGION_PROFILES.get(region, {}).get("moq_threshold", 1)
    if quantity >= moq: return 0.0
    return min(0.5, (moq - quantity) / max(moq, 1) * 0.6)


def _best_custom_region(process, material, delivery_country, regions=None):
    eval_regions = regions if regions is not None else REGION_PROFILES
    best_region, best_score = "Local", -1
    for region, profile in eval_regions.items():
        if not _capability_match(region, process):
            continue
        pf = profile.get("process_fit", {}).get(process, 0.3)
        mf = profile.get("material_fit", {}).get(material, 0.3)
        cost_adv = 1.0 - profile.get("base_cost_mult", 1.0)
        score = pf * 0.4 + mf * 0.3 + cost_adv * 0.3
        if score > best_score:
            best_score = score
            best_region = region
    return best_region


# ══════════════════════════════════════════════════════════
# SCORING (with urgency)
# ══════════════════════════════════════════════════════════

def compute_score(avg_cost, lead_time, uncertainty, quality, quantity_fit,
                  process_fit, moq_penalty_val, max_cost, max_lead,
                  priority="cost"):
    nc = avg_cost / max(max_cost, 1)
    nl = lead_time / max(max_lead, 1)
    nq = 1 - quality; nqf = 1 - quantity_fit; npf = 1 - process_fit

    # Dynamic weights based on urgency
    if priority == "speed":
        w = {"cost": 0.25, "lead": 0.30, "unc": 0.10, "qual": 0.08, "qty": 0.12, "proc": 0.10, "moq": 0.05}
    else:  # cost
        w = {"cost": 0.38, "lead": 0.12, "unc": 0.12, "qual": 0.08, "qty": 0.15, "proc": 0.10, "moq": 0.05}

    score = (w["cost"] * nc + w["lead"] * nl + w["unc"] * uncertainty +
             w["qual"] * nq + w["qty"] * nqf + w["proc"] * npf + w["moq"] * moq_penalty_val)
    return {"total_score": round(score, 6), "weights_used": priority}


# ══════════════════════════════════════════════════════════
# PER-PART EVALUATION (uses get_price + DB)
# ══════════════════════════════════════════════════════════

def evaluate_part(part, delivery_country, vendor_memories, db: Session,
                  priority="cost", external_price=None, regions=None):
    from app.services.pricing_service import get_price, is_custom_part

    name = part.get("description", part.get("part_name", "Unknown"))
    category = part.get("category", "standard")
    quantity = part.get("quantity", 1)
    material = part.get("material", "")
    combined = f"{name} {material}"
    process = _detect_process(combined)
    mat_family = _detect_material(combined)
    weight = _est_weight(name, category, quantity)

    # ── CUSTOM PART: skip pricing, return manufacturing intelligence ──
    if is_custom_part(part):
        best_region = _best_custom_region(process, mat_family, delivery_country, regions)
        profile = (regions or REGION_PROFILES).get(best_region, REGION_PROFILES.get("Local", {}))
        lead_days = profile.get("lead_days_base", 14) + 3
        risk = calculate_risk(best_region, vendor_memories.get(best_region), "high")
        return {
            "part_name": name, "category": category, "quantity": quantity,
            "detected_process": process, "detected_material": mat_family,
            "price_source": "custom_rfq_required", "unit_price": 0,
            "best_region": best_region, "best_cost": 0,
            "best_lead_days": lead_days, "best_score": 0,
            "quantity_fit": 1.0, "process_fit": 1.0, "logistics_per_unit": 0,
            "risk": risk, "cost_range": [0, 0],
            "alternative_region": None, "alternative_cost": None,
            "is_custom": True, "rfq_required": True, "drawing_required": True,
            "manufacturing_intelligence": {
                "detected_process": process, "recommended_process": process,
                "material": mat_family, "suggested_region": best_region,
                "drawing_required": True, "quote_required": True,
            },
        }

    # DB-first pricing (standard parts only)
    price_data = get_price({"part_name": name, "material": material,
                             "quantity": quantity, "mpn": part.get("mpn", "")}, db)
    base_unit = external_price if (external_price and external_price > 0) else price_data["price"]
    if base_unit is None:
        base_unit = 0
    base_total = base_unit * quantity
    price_source = "external" if external_price else price_data.get("source", "fallback")

    complexity = "high" if category == "custom" and quantity > 100 else ("medium" if category == "custom" else "low")

    candidates = []
    eval_regions = regions if regions is not None else REGION_PROFILES
    for region, profile in eval_regions.items():
        # ── CAPABILITY FILTER ──
        if not _capability_match(region, process):
            continue  # reject incompatible supplier

        mfg_cost = base_total * profile["base_cost_mult"]
        logistics = calculate_logistics_cost(weight, region, delivery_country, urgency=priority)
        tariff = calculate_tariff_cost(mfg_cost, region, delivery_country)
        total_cost = mfg_cost + logistics["total_logistics"] + tariff["total_tariff"]

        risk = calculate_risk(region, vendor_memories.get(region), complexity)
        cost_range = calculate_cost_range(total_cost, risk["uncertainty"])
        lead_days = profile["lead_days_base"] + logistics["transit_days"]

        # Quantity fit (hard override at 500+)
        if quantity >= QTY_OVERRIDE:
            qf = 1.0 if profile["base_cost_mult"] <= 0.42 else (0.7 if profile["base_cost_mult"] <= 0.6 else 0.3)
        elif quantity < profile["moq_threshold"]:
            qf = max(0.2, 1.0 - (profile["moq_threshold"] - quantity) / max(profile["moq_threshold"], 1) * 0.8)
        elif region == "Local":
            qf = 1.0 if quantity < 50 else 0.7
        else:
            qf = 0.85

        pf = profile.get("process_fit", {}).get(process, 0.5)
        mf = profile.get("material_fit", {}).get(mat_family, 0.5)
        combined_pf = pf * 0.6 + mf * 0.4
        moq_pen = _moq_penalty(region, quantity)

        candidates.append({
            "region": region, "manufacturing_cost": round(mfg_cost, 2),
            "logistics": logistics, "tariff": tariff,
            "total_base_cost": round(total_cost, 2), "cost_range": cost_range,
            "risk": risk, "total_lead_days": lead_days,
            "quality_score": profile["quality_score"], "quantity_fit": qf,
            "process_fit": combined_pf, "moq_penalty": moq_pen,
        })

    if not candidates:
        # Fallback: add Local if everything filtered out
        candidates.append(_build_local_fallback(base_total, weight, delivery_country, vendor_memories, complexity))

    mc = max(c["cost_range"]["average"] for c in candidates) or 1
    ml = max(c["total_lead_days"] for c in candidates) or 1
    for c in candidates:
        c["score"] = compute_score(
            c["cost_range"]["average"], c["total_lead_days"],
            c["risk"]["uncertainty"], c["quality_score"],
            c["quantity_fit"], c["process_fit"], c["moq_penalty"],
            mc, ml, priority)

    best = min(candidates, key=lambda c: c["score"]["total_score"])
    alt_cands = sorted(candidates, key=lambda c: c["score"]["total_score"])
    alt = alt_cands[1] if len(alt_cands) > 1 else None

    per_unit_log = best["logistics"]["total_logistics"] / max(quantity, 1)

    return {
        "part_name": name, "category": category, "quantity": quantity,
        "detected_process": process, "detected_material": mat_family,
        "price_source": price_source, "unit_price": round(base_unit, 4),
        "best_region": best["region"], "best_cost": best["cost_range"]["average"],
        "best_lead_days": best["total_lead_days"],
        "best_score": best["score"]["total_score"],
        "quantity_fit": best["quantity_fit"], "process_fit": best["process_fit"],
        "logistics_per_unit": round(per_unit_log, 4),
        "risk": best["risk"],
        "cost_range": [best["cost_range"]["low"], best["cost_range"]["high"]],
        "alternative_region": alt["region"] if alt else None,
        "alternative_cost": alt["cost_range"]["average"] if alt else None,
    }


def _build_local_fallback(base_total, weight, dc, vm, complexity):
    p = REGION_PROFILES["Local"]
    mc = base_total * p["base_cost_mult"]
    lg = calculate_logistics_cost(weight, "Local", dc)
    tf = calculate_tariff_cost(mc, "Local", dc)
    tc = mc + lg["total_logistics"] + tf["total_tariff"]
    rk = calculate_risk("Local", vm.get("Local"), complexity)
    cr = calculate_cost_range(tc, rk["uncertainty"])
    return {"region": "Local", "manufacturing_cost": round(mc, 2), "logistics": lg,
            "tariff": tf, "total_base_cost": round(tc, 2), "cost_range": cr,
            "risk": rk, "total_lead_days": p["lead_days_base"] + lg["transit_days"],
            "quality_score": p["quality_score"], "quantity_fit": 0.7,
            "process_fit": 0.6, "moq_penalty": 0.0}


# ══════════════════════════════════════════════════════════
# GLOBAL OPTIMIZER (evaluates BOM-wide combinations)
# ══════════════════════════════════════════════════════════

def global_optimize(part_decisions: List[Dict], delivery_country: str) -> Dict:
    """
    Compare: naive all-local vs per-part-optimized vs top-2-region-consolidated.
    Pick the plan with lowest TOTAL BOM cost.
    """
    # Strategy A: Per-part optimized (current)
    optimized_cost = sum(pd["best_cost"] for pd in part_decisions)

    # Strategy B: All local (naive baseline)
    naive_cost = 0
    for pd in part_decisions:
        # Local cost ≈ optimized / local_mult * 0.95
        local_mult = REGION_PROFILES["Local"]["base_cost_mult"]
        best_mult = REGION_PROFILES.get(pd["best_region"], {}).get("base_cost_mult", 1.0)
        naive_cost += pd["best_cost"] * (local_mult / max(best_mult, 0.1))

    # Strategy C: Consolidated (top-2 regions by part count)
    region_counts = defaultdict(int)
    for pd in part_decisions:
        region_counts[pd["best_region"]] += 1
    top_regions = sorted(region_counts, key=region_counts.get, reverse=True)[:2]

    consolidated_cost = 0
    for pd in part_decisions:
        if pd["best_region"] in top_regions:
            consolidated_cost += pd["best_cost"]
        else:
            # Re-route to nearest top region
            best_top = min(top_regions, key=lambda r: REGION_PROFILES.get(r, {}).get("base_cost_mult", 1))
            rr_mult = REGION_PROFILES.get(best_top, {}).get("base_cost_mult", 0.5)
            orig_mult = REGION_PROFILES.get(pd["best_region"], {}).get("base_cost_mult", 0.5)
            consolidated_cost += pd["best_cost"] * (rr_mult / max(orig_mult, 0.1))

    # Pick best strategy
    strategies = {
        "per_part_optimized": round(optimized_cost, 2),
        "all_local_naive": round(naive_cost, 2),
        "consolidated_top2": round(consolidated_cost, 2),
    }
    best_strategy = min(strategies, key=strategies.get)

    true_savings = round((1 - strategies[best_strategy] / max(naive_cost, 1)) * 100, 1) if naive_cost > 0 else 0

    return {
        "best_strategy_name": best_strategy,
        "strategies_compared": strategies,
        "true_savings_vs_local": true_savings,
        "naive_local_cost": round(naive_cost, 2),
        "optimized_cost": round(strategies[best_strategy], 2),
    }


# ══════════════════════════════════════════════════════════
# EXPLANATION ENGINE
# ══════════════════════════════════════════════════════════

def generate_explanation(global_result, part_decisions, region_distribution, delivery_country, priority, currency="USD"):
    reasons = []
    sav = global_result.get("true_savings_vs_local", 0)
    opt_cost = global_result.get("optimized_cost", 0)
    naive_cost = global_result.get("naive_local_cost", 0)

    if sav > 0:
        reasons.append(f"{sav}% total savings vs all-local strategy ({currency} {naive_cost:,.0f} → {currency} {opt_cost:,.0f})")

    total_qty = sum(pd["quantity"] for pd in part_decisions)
    if total_qty >= QTY_OVERRIDE:
        offshore = [pd for pd in part_decisions if pd["best_region"] not in ("Local", "USA", "EU (Germany)", delivery_country)]
        if offshore:
            avg_log = sum(pd["logistics_per_unit"] for pd in offshore) / max(len(offshore), 1)
            reasons.append(f"At {total_qty:,} units, offshore handles {len(offshore)} parts — avg logistics {currency} {avg_log:.3f}/unit")

    if priority == "speed":
        avg_lead = sum(pd["best_lead_days"] for pd in part_decisions) / max(len(part_decisions), 1)
        reasons.append(f"Speed priority: avg lead time {avg_lead:.0f} days (fast regions preferred)")

    top_region = max(region_distribution, key=region_distribution.get) if region_distribution else "Local"
    pct = region_distribution.get(top_region, 0)
    reasons.append(f"Primary sourcing: {top_region} ({pct:.0f}% of cost)")

    procs = defaultdict(int)
    for pd in part_decisions:
        procs[pd["detected_process"]] += 1
    top_proc = max(procs, key=procs.get) if procs else "mixed"
    reasons.append(f"Dominant process: {top_proc} ({procs[top_proc]} parts)")

    if not reasons:
        reasons.append(f"Best global optimization for {len(part_decisions)} parts")

    summary = (f"Global strategy: {global_result['best_strategy_name'].replace('_', ' ')}. "
               f"{len(part_decisions)} parts, {currency} {opt_cost:,.2f} total. "
               f"Saves {sav}% vs all-local.")

    return {"reasons": reasons, "decision_summary": summary}


# ══════════════════════════════════════════════════════════
# MAIN ENTRY
# ══════════════════════════════════════════════════════════

def build_strategy_output(analyzer_output: Dict, delivery_location: str = "India",
                          vendor_memories: Optional[Dict] = None,
                          pricing_history: Optional[List] = None,
                          external_pricing: Optional[Dict] = None,
                          db: Session = None,
                          priority: str = "cost",
                          target_currency: str = "USD") -> Dict:
    """
    MAIN ENTRY. DB-integrated, globally optimized.
    priority: "cost" or "speed"
    """
    vendor_memories = vendor_memories or {}
    pricing_history = pricing_history or []
    external_pricing = external_pricing or {}

    s2 = analyzer_output.get("section_2_component_breakdown", [])
    delivery_country = _extract_country(delivery_location)

    # Categorize
    categories = defaultdict(int)
    total_quantity = 0
    for item in s2:
        categories[item.get("category", "standard")] += 1
        total_quantity += item.get("quantity", 1)

    bom_summary = {"total_parts": len(s2), "total_quantity": total_quantity,
                   "categories": dict(categories), "priority": priority}

    # ═══ Merge "Local" into delivery country region ═══
    # If user is in India, "Local" and "India" are the same — don't double-count.
    # Replace "Local" profile with the delivery country's profile when they match.
    if delivery_country in REGION_PROFILES and delivery_country != "Local":
        # User's country has a dedicated profile — skip the generic "Local"
        active_regions = {k: v for k, v in REGION_PROFILES.items() if k != "Local"}
    else:
        active_regions = REGION_PROFILES

    # ═══ Per-part evaluation (with DB pricing) ═══
    part_decisions = []
    for item in s2:
        iid = item.get("item_id", "")
        ext = external_pricing.get(iid, {})
        ext_price = ext.get("best_price") if ext else None

        pd = evaluate_part(item, delivery_country, vendor_memories, db,
                           priority=priority, external_price=ext_price,
                           regions=active_regions)
        part_decisions.append(pd)

    # ═══ Region distribution ═══
    region_cost = defaultdict(float)
    for pd in part_decisions:
        region_cost[pd["best_region"]] += pd["best_cost"]
    total_cost = sum(region_cost.values()) or 1
    region_distribution = {r: round(c / total_cost * 100, 1) for r, c in region_cost.items()}

    # ═══ Global optimization ═══
    global_result = global_optimize(part_decisions, delivery_country)

    # ═══ Explanation ═══
    explanation = generate_explanation(global_result, part_decisions, region_distribution,
                                       delivery_country, priority, currency=target_currency)

    # ═══ Risk aggregation ═══
    avg_unc = sum(pd["risk"]["uncertainty"] for pd in part_decisions) / max(len(part_decisions), 1)
    avg_var = sum(pd["risk"]["delivery_variance_days"] for pd in part_decisions) / max(len(part_decisions), 1)
    new_vendor_pct = sum(1 for pd in part_decisions if pd["risk"]["is_new_vendor"]) / max(len(part_decisions), 1)

    # ═══ Procurement plan ═══
    plan = _build_procurement_plan(part_decisions, delivery_country)

    # ═══ Build output ═══
    top_region = max(region_cost, key=region_cost.get) if region_cost else "Local"
    rec_cost = global_result["optimized_cost"]
    cr = calculate_cost_range(rec_cost, avg_unc)

    return {
        "procurement_strategy": {
            "region_plan": plan,
            "vendor_plan": {"recommended_suppliers": len(plan), "regions": list(region_distribution.keys())},
            "timeline": {"min_days": min(pd["best_lead_days"] for pd in part_decisions) if part_decisions else 7,
                         "max_days": max(pd["best_lead_days"] for pd in part_decisions) if part_decisions else 28,
                         "avg_days": round(sum(pd["best_lead_days"] for pd in part_decisions) / max(len(part_decisions), 1))},
            "cost_summary": {"range": [cr["low"], cr["high"]], "average": cr["average"],
                             "savings_percent": global_result["true_savings_vs_local"],
                             "savings_value": round(global_result["naive_local_cost"] - rec_cost, 2)},
            "risk_analysis": {"overall_uncertainty": round(avg_unc, 3),
                              "avg_delivery_variance_days": round(avg_var, 1),
                              "new_vendor_pct": round(new_vendor_pct * 100, 1),
                              "risk_level": "HIGH" if avg_unc > 0.30 else ("MEDIUM" if avg_unc > 0.15 else "LOW")},
        },
        "recommended_strategy": {
            "location": top_region, "cost_range": [cr["low"], cr["high"]],
            "average_cost": cr["average"],
            "savings_percent": global_result["true_savings_vs_local"],
            "lead_time": round(sum(pd["best_lead_days"] for pd in part_decisions) / max(len(part_decisions), 1)),
            "reasons": explanation["reasons"],
        },
        "alternative_strategies": [
            {"name": k, "total_cost": v}
            for k, v in global_result["strategies_compared"].items()
            if k != global_result["best_strategy_name"]
        ],
        "global_optimization": global_result,
        "region_distribution": region_distribution,
        "part_level_decisions": [
            {
                "part_name": pd["part_name"], "category": pd["category"], "quantity": pd["quantity"],
                "process": pd["detected_process"], "material": pd["detected_material"],
                "price_source": pd["price_source"], "unit_price": pd["unit_price"],
                "best_region": pd["best_region"], "best_cost": round(pd["best_cost"], 2),
                "lead_days": pd["best_lead_days"], "quantity_fit": round(pd["quantity_fit"], 3),
                "process_fit": round(pd["process_fit"], 3),
                "logistics_per_unit": pd["logistics_per_unit"],
                "cost_range": pd["cost_range"],
                "alternative_region": pd["alternative_region"],
                "is_custom": pd.get("is_custom", False),
                "rfq_required": pd.get("rfq_required", False),
                "drawing_required": pd.get("drawing_required", False),
                "manufacturing_intelligence": pd.get("manufacturing_intelligence"),
            }
            for pd in part_decisions
        ],
        "bom_summary": bom_summary,
        "explanation": explanation["decision_summary"],
        "decision_summary": explanation["decision_summary"],
    }


def _build_procurement_plan(part_decisions, delivery_country):
    groups = defaultdict(list)
    for pd in part_decisions:
        groups[pd["best_region"]].append(pd)
    total_cost = sum(pd["best_cost"] for pd in part_decisions) or 1
    plan = []
    for region, parts in sorted(groups.items(), key=lambda x: -sum(p["best_cost"] for p in x[1])):
        rc = sum(p["best_cost"] for p in parts)
        tq = sum(p["quantity"] for p in parts)
        procs = defaultdict(int)
        for p in parts: procs[p["detected_process"]] += 1
        top_proc = max(procs, key=procs.get)
        plan.append({
            "region": region, "parts_count": len(parts),
            "parts": [p["part_name"][:60] for p in parts],
            "dominant_process": top_proc, "total_quantity": tq,
            "estimated_cost": round(rc, 2),
            "percentage": round(rc / total_cost * 100, 1),
            "reason": f"Optimized for {top_proc} at {tq:,} units in {region}",
        })
    return plan


def _extract_country(loc):
    parts = [p.strip() for p in loc.split(",")]
    if parts:
        m = {"india": "India", "usa": "USA", "us": "USA", "china": "China",
             "germany": "EU (Germany)", "eu": "EU (Germany)", "mexico": "Mexico",
             "vietnam": "Vietnam", "japan": "Japan", "south korea": "South Korea",
             "taiwan": "Taiwan", "thailand": "Thailand"}
        return m.get(parts[-1].strip().lower(), "Local")
    return "Local"