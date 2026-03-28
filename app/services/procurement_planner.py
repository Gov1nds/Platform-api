"""
Procurement Planner FINAL — Global Procurement Plan Generator

Takes strategy_output → produces execution-ready procurement plan:
  - Process-aware supplier consolidation
  - Local vs offshore split
  - Currency normalization
  - Risk-adjusted timeline
  - Execution steps
"""
import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

logger = logging.getLogger("procurement_planner")

FOREX_RATES = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.5, "CNY": 7.25,
    "JPY": 155.0, "KRW": 1350.0, "MXN": 17.2, "THB": 36.0, "VND": 24800.0,
    "TWD": 31.5, "CAD": 1.37, "AUD": 1.55,
}

LOCAL_REGIONS = {"Local", "USA", "EU (Germany)", "Mexico"}


def _get_forex_rates_from_db():
    """Try to load forex rates from DB. Returns hardcoded fallback on failure."""
    try:
        from app.core.database import SessionLocal
        from app.services.geo_service import get_forex_rates
        db = SessionLocal()
        try:
            rates = get_forex_rates(db)
            if rates:
                return rates
        finally:
            db.close()
    except Exception:
        pass
    return FOREX_RATES


def convert_currency(amount: float, from_c: str, to_c: str) -> float:
    if from_c == to_c: return amount
    rates = _get_forex_rates_from_db()
    return round(amount / rates.get(from_c, 1) * rates.get(to_c, 1), 2)


def normalize_costs(strategy: Dict, target: str) -> Dict:
    """Normalize all cost fields to target currency."""
    if target == "USD":
        strategy["currency"] = "USD"
        return strategy
    rate = FOREX_RATES.get(target, 1)
    def _c(v): return round(v * rate, 2) if isinstance(v, (int, float)) else v

    # Procurement strategy
    ps = strategy.get("procurement_strategy", {})
    cs = ps.get("cost_summary", {})
    for k in ("average", "savings_value"): 
        if k in cs: cs[k] = _c(cs[k])
    r = cs.get("range", [])
    if isinstance(r, list) and len(r) == 2: cs["range"] = [_c(r[0]), _c(r[1])]

    rec = strategy.get("recommended_strategy", {})
    if rec.get("average_cost"): rec["average_cost"] = _c(rec["average_cost"])
    cr = rec.get("cost_range", [])
    if isinstance(cr, list) and len(cr) == 2: rec["cost_range"] = [_c(cr[0]), _c(cr[1])]

    for p in ps.get("region_plan", []):
        if "estimated_cost" in p: p["estimated_cost"] = _c(p["estimated_cost"])

    strategy["currency"] = target
    strategy["forex_rate_to_usd"] = round(1 / rate, 6)
    return strategy


def consolidate_suppliers(plan: List[Dict], max_suppliers: int = 5) -> Dict:
    """Merge small groups into larger ones by process affinity."""
    if len(plan) <= max_suppliers:
        return {"optimized_plan": plan, "consolidation_actions": [],
                "original_count": len(plan), "final_count": len(plan), "savings": 0}

    sorted_plan = sorted(plan, key=lambda x: -x.get("estimated_cost", 0))
    keep = sorted_plan[:max_suppliers - 1]
    merge = sorted_plan[max_suppliers - 1:]

    actions = []
    for g in merge:
        gp = g.get("dominant_process", "")
        target = next((k for k in keep if k.get("dominant_process") == gp), keep[0])
        target["parts"].extend(g.get("parts", []))
        target["parts_count"] += g.get("parts_count", 0)
        target["estimated_cost"] = round(target.get("estimated_cost", 0) + g.get("estimated_cost", 0), 2)
        target["total_quantity"] = target.get("total_quantity", 0) + g.get("total_quantity", 0)
        actions.append({"from": g["region"], "to": target["region"],
                        "parts_moved": g.get("parts_count", 0), "process": gp,
                        "savings_est": round(g.get("parts_count", 1) * 12, 2)})

    tc = sum(k.get("estimated_cost", 0) for k in keep) or 1
    for k in keep: k["percentage"] = round(k.get("estimated_cost", 0) / tc * 100, 1)

    return {"optimized_plan": keep, "consolidation_actions": actions,
            "original_count": len(plan) + len(merge), "final_count": len(keep),
            "savings": round(sum(a["savings_est"] for a in actions), 2)}


def split_local_offshore(part_decisions: List[Dict]) -> Dict:
    local, offshore = [], []
    for pd in part_decisions:
        e = {"part_name": pd["part_name"], "category": pd["category"],
             "quantity": pd["quantity"], "region": pd["best_region"],
             "process": pd.get("process", pd.get("detected_process", "")),
             "cost": round(pd["best_cost"], 2)}
        (local if pd["best_region"] in LOCAL_REGIONS else offshore).append(e)
    lc = sum(p["cost"] for p in local)
    oc = sum(p["cost"] for p in offshore)
    t = lc + oc or 1
    return {
        "local": {"count": len(local), "cost": round(lc, 2), "pct": round(lc / t * 100, 1), "parts": local},
        "offshore": {"count": len(offshore), "cost": round(oc, 2), "pct": round(oc / t * 100, 1), "parts": offshore},
        "ratio": f"{len(local)}:{len(offshore)}",
    }


def generate_procurement_plan(strategy_output: Dict, target_currency: str = "USD",
                               max_suppliers: int = 5) -> Dict:
    """
    MAIN ENTRY. Takes strategy_output → execution-ready procurement plan.
    """
    ps = strategy_output.get("procurement_strategy", {})
    raw_plan = ps.get("region_plan", strategy_output.get("procurement_plan", []))
    part_decisions = strategy_output.get("part_level_decisions", [])

    consolidated = consolidate_suppliers(raw_plan, max_suppliers)
    split = split_local_offshore(part_decisions)

    strategy_output = normalize_costs(strategy_output, target_currency)

    risk = ps.get("risk_analysis", strategy_output.get("risk_analysis", {}))
    unc = risk.get("overall_uncertainty", 0)
    risk_level = "HIGH" if unc > 0.30 else ("MEDIUM" if unc > 0.15 else "LOW")

    timeline = ps.get("timeline", {})
    global_opt = strategy_output.get("global_optimization", {})

    return {
        "procurement_plan": consolidated["optimized_plan"],
        "consolidation_report": {
            "actions": consolidated["consolidation_actions"],
            "original_suppliers": consolidated["original_count"],
            "final_suppliers": consolidated["final_count"],
            "logistics_savings": consolidated["savings"],
        },
        "local_vs_offshore": split,
        "region_distribution": strategy_output.get("region_distribution", {}),
        "recommended_strategy": strategy_output.get("recommended_strategy", {}),
        "alternative_strategies": strategy_output.get("alternative_strategies", []),
        "global_optimization": global_opt,
        "cost_summary": ps.get("cost_summary", strategy_output.get("cost_summary", {})),
        "risk_analysis": {**risk, "overall_risk_level": risk_level},
        "timeline": timeline,
        "explanation": strategy_output.get("explanation", ""),
        "decision_summary": strategy_output.get("decision_summary", ""),
        "currency": target_currency,
        "execution_steps": [
            "1. Review region plan and confirm part assignments",
            "2. Submit RFQ to PGI for custom/machined parts",
            "3. Order standard components from recommended distributors",
            "4. Consolidate shipments per region cluster",
            "5. Confirm lead times with production schedule",
            "6. Track milestones: T0→T1→T2→T3→T4",
            "7. Submit feedback on delivery for learning system",
        ],
    }
