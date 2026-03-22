"""
Memory Service v3 — Supplier Learning Loop

UPGRADES:
  - update_from_rfq_completion(): auto-called when RFQ completes
  - adjust_future_confidence(): modifies uncertainty based on history
  - Pricing accuracy tracking per vendor
  - Decay old data (>180 days → pull toward neutral)
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from app.models.memory import SupplierMemory
from app.models.vendor import Vendor
from app.models.tracking import ExecutionFeedback
from app.models.pricing import PricingHistory
from app.models.rfq import RFQ

logger = logging.getLogger("memory_service")

EMA_ALPHA = 0.3


def get_vendor_memory(db: Session, vendor_id: str) -> Optional[Dict]:
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem: return None
    return {"vendor_id": mem.vendor_id, "performance_score": mem.performance_score,
            "cost_accuracy_score": mem.cost_accuracy_score,
            "delivery_accuracy_score": mem.delivery_accuracy_score,
            "risk_level": mem.risk_level, "total_orders": mem.total_orders,
            "avg_cost_delta_pct": mem.avg_cost_delta_pct,
            "avg_lead_delta_days": mem.avg_lead_delta_days}


def get_all_memories(db: Session) -> Dict[str, Dict]:
    result = {}
    for m in db.query(SupplierMemory).all():
        v = db.query(Vendor).filter(Vendor.id == m.vendor_id).first()
        if v:
            result[v.region] = {
                "vendor_id": m.vendor_id, "total_orders": m.total_orders,
                "cost_accuracy_score": m.cost_accuracy_score,
                "delivery_accuracy_score": m.delivery_accuracy_score,
                "performance_score": m.performance_score, "risk_level": m.risk_level}
    return result


def update_supplier_scores(db: Session, vendor_id: str,
                            actual_cost=None, predicted_cost=None,
                            actual_lead=None, predicted_lead=None,
                            quality_ok=True) -> Dict:
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return {"status": "not_found"}

    mem.total_orders = (mem.total_orders or 0) + 1
    changes = {}

    if actual_cost is not None and predicted_cost and predicted_cost > 0:
        delta_pct = (actual_cost - predicted_cost) / predicted_cost * 100
        mem.avg_cost_delta_pct = round((mem.avg_cost_delta_pct or 0) * (1 - EMA_ALPHA) + delta_pct * EMA_ALPHA, 2)
        mem.cost_accuracy_score = round(max(0, min(1, 1.0 - abs(mem.avg_cost_delta_pct) / 50)), 3)
        changes["cost_delta_pct"] = round(delta_pct, 2)

    if actual_lead is not None and predicted_lead and predicted_lead > 0:
        delta = actual_lead - predicted_lead
        mem.avg_lead_delta_days = round((mem.avg_lead_delta_days or 0) * (1 - EMA_ALPHA) + delta * EMA_ALPHA, 2)
        mem.delivery_accuracy_score = round(max(0, min(1, 1.0 - abs(mem.avg_lead_delta_days) / 14)), 3)
        changes["lead_delta_days"] = round(delta, 1)

    q_mult = 1.0 if quality_ok else 0.85
    mem.performance_score = round(mem.cost_accuracy_score * 0.4 + mem.delivery_accuracy_score * 0.4 + q_mult * 0.2, 3)
    mem.risk_level = round(max(0, min(1, 1.0 - mem.performance_score)), 3)
    mem.last_updated = datetime.utcnow()
    db.commit()

    changes.update({"performance": mem.performance_score, "risk": mem.risk_level, "orders": mem.total_orders})
    logger.info(f"Vendor {vendor_id} memory updated: {changes}")
    return {"status": "updated", "changes": changes}


def update_from_rfq_completion(db: Session, rfq_id: str,
                                actual_cost: float, actual_lead_days: float,
                                quality_ok: bool = True) -> Dict:
    """Called when an RFQ reaches completion. Updates vendor memory + records pricing."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return {"status": "rfq_not_found"}

    vendor_id = rfq.selected_vendor_id
    if not vendor_id:
        return {"status": "no_vendor_selected"}

    predicted_cost = rfq.total_estimated_cost or 0
    predicted_lead = 14.0

    # Update memory
    result = update_supplier_scores(
        db, vendor_id, actual_cost, predicted_cost,
        actual_lead_days, predicted_lead, quality_ok)

    # Record pricing for each item
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor and rfq.items:
        per_item_cost = actual_cost / max(len(rfq.items), 1)
        for item in rfq.items:
            db.add(PricingHistory(
                vendor_id=vendor_id, part_name=item.part_name or "",
                material=item.material or "", quantity=item.quantity or 1,
                price=round(per_item_cost, 2), region=vendor.region or "",
                currency="USD"))
        db.commit()

    return result


def adjust_future_confidence(db: Session, vendor_id: str) -> Dict:
    """Compute adjusted confidence for future strategy decisions."""
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return {"uncertainty_adjustment": 0.30}  # unknown vendor

    n = mem.total_orders or 0
    perf = mem.performance_score or 0.5

    # More orders + better perf → lower uncertainty
    if n >= 20 and perf >= 0.8:
        adj = 0.05
    elif n >= 10 and perf >= 0.6:
        adj = 0.10
    elif n >= 5:
        adj = 0.15
    elif n >= 1:
        adj = 0.22
    else:
        adj = 0.30

    # Penalize bad accuracy
    cost_acc = mem.cost_accuracy_score or 0.5
    if cost_acc < 0.5:
        adj += 0.10

    return {
        "vendor_id": vendor_id,
        "uncertainty_adjustment": round(min(0.50, adj), 3),
        "based_on_orders": n,
        "performance": perf,
    }


def decay_old_data(db: Session, days_threshold: int = 180) -> Dict:
    cutoff = datetime.utcnow() - timedelta(days=days_threshold)
    old = db.query(SupplierMemory).filter(SupplierMemory.last_updated < cutoff).all()
    decay_factor = 0.1
    count = 0
    for m in old:
        m.cost_accuracy_score = round(m.cost_accuracy_score * (1 - decay_factor) + 0.5 * decay_factor, 3)
        m.delivery_accuracy_score = round(m.delivery_accuracy_score * (1 - decay_factor) + 0.5 * decay_factor, 3)
        m.performance_score = round((m.cost_accuracy_score + m.delivery_accuracy_score) / 2, 3)
        m.risk_level = round(1.0 - m.performance_score, 3)
        count += 1
    if count:
        db.commit()
        logger.info(f"Decayed {count} stale memories (>{days_threshold}d)")
    return {"decayed": count}


def get_system_stats(db: Session) -> Dict:
    mems = db.query(SupplierMemory).all()
    vendors = {v.id: v for v in db.query(Vendor).all()}
    total_orders = sum(m.total_orders or 0 for m in mems)

    high_perf = []
    risky = []
    for m in mems:
        v = vendors.get(m.vendor_id)
        if not v: continue
        entry = {"vendor": v.name, "region": v.region, "orders": m.total_orders,
                 "performance": m.performance_score, "risk": m.risk_level}
        if m.performance_score >= 0.75: high_perf.append(entry)
        if m.risk_level >= 0.4: risky.append(entry)

    avg_p = sum(m.performance_score for m in mems) / max(len(mems), 1)
    return {
        "active_vendors": len(mems),
        "total_orders_tracked": total_orders,
        "average_performance": round(avg_p, 3),
        "high_performers": sorted(high_perf, key=lambda x: -x["performance"])[:5],
        "risky_vendors": sorted(risky, key=lambda x: -x["risk"])[:5],
        "maturity": "early" if total_orders < 10 else ("learning" if total_orders < 50 else "mature"),
    }
