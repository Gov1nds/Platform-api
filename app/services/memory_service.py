"""
Memory Service v4 — Supplier Learning Loop

FIXES:
  - update_from_rfq_completion() no longer distributes cost evenly across items
  - Uses per-item quoted_price for pricing records when available
  - Gets predicted_lead from analysis, not hardcoded
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from app.models.memory import SupplierMemory
from app.models.vendor import Vendor
from app.models.tracking import ExecutionFeedback
from app.models.pricing import PricingQuote as PricingHistory
from app.models.analysis import AnalysisResult
from app.models.rfq import RFQBatch as RFQ, RFQItem

logger = logging.getLogger("memory_service")

EMA_ALPHA = 0.3


def get_vendor_memory(db: Session, vendor_id: str) -> Optional[Dict]:
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return None
    return {"vendor_id": mem.vendor_id, "performance_score": mem.performance_score,
            "cost_accuracy_score": mem.cost_accuracy_score,
            "delivery_accuracy_score": mem.delivery_accuracy_score,
            "risk_level": mem.risk_level, "total_orders": mem.total_orders,
            "avg_cost_delta_pct": mem.avg_cost_delta_pct,
            "avg_lead_delta_days": mem.avg_lead_delta_days}


def get_all_memories(db: Session) -> Dict[str, Dict]:
    # FIXED: use joined query (same fix as vendor_service)
    results = (
        db.query(SupplierMemory, Vendor)
        .join(Vendor, SupplierMemory.vendor_id == Vendor.id)
        .all()
    )
    return {
        vendor.region: {
            "vendor_id": mem.vendor_id, "total_orders": mem.total_orders,
            "cost_accuracy_score": mem.cost_accuracy_score,
            "delivery_accuracy_score": mem.delivery_accuracy_score,
            "performance_score": mem.performance_score, "risk_level": mem.risk_level,
        }
        for mem, vendor in results
    }


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
    risk_score = round(max(0, min(1, 1.0 - mem.performance_score)), 3)
    mem.risk_level = "low" if risk_score < 0.2 else ("high" if risk_score > 0.5 else "medium")
    mem.last_updated = datetime.utcnow()
    db.flush()

    changes.update({"performance": mem.performance_score, "risk": mem.risk_level, "risk_score": risk_score, "orders": mem.total_orders})
    logger.info(f"Vendor {vendor_id} memory updated: {changes}")
    return {"status": "updated", "changes": changes}


def update_from_rfq_completion(db: Session, rfq_id: str,
                                actual_cost: float, actual_lead_days: float,
                                quality_ok: bool = True) -> Dict:
    """
    FIXED: No longer distributes cost evenly. Uses per-item quoted prices
    when available. Gets predicted_lead from analysis.
    """
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return {"status": "rfq_not_found"}

    vendor_id = rfq.selected_vendor_id
    if not vendor_id:
        return {"status": "no_vendor_selected"}

    predicted_cost = rfq.total_estimated_cost or 0

    # FIXED: Get predicted_lead from analysis, not hardcoded 14
    predicted_lead = 14.0
    if rfq.bom_id:
        analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == rfq.bom_id).first()
        if analysis and analysis.lead_time:
            predicted_lead = float(analysis.lead_time)

    # Update memory
    result = update_supplier_scores(
        db, vendor_id, actual_cost, predicted_cost,
        actual_lead_days, predicted_lead, quality_ok)

    # FIXED: Record pricing per item using actual item quotes, not evenly distributed
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor:
        items = db.query(RFQItem).filter(RFQItem.rfq_id == rfq.id).all()
        for item in items:
            # Use the item's actual quoted price if available
            item_price = item.final_price or item.quoted_price
            if item_price and item_price > 0:
                from app.services.pricing_service import _normalize_for_lookup, _save_price
                _save_price(
                    db,
                    norm_name=_normalize_for_lookup(item.part_name or ""),
                    mpn="",
                    material=item.material or "",
                    quantity=item.quantity or 1,
                    price=round(item_price, 2),
                    source_type="rfq_actual",
                    vendor_id=vendor_id,
                    currency=rfq.currency or "USD",
                )

    return result


def adjust_future_confidence(db: Session, vendor_id: str) -> Dict:
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return {"uncertainty_adjustment": 0.30}
    n = mem.total_orders or 0
    perf = mem.performance_score or 0.5
    if n >= 20 and perf >= 0.8: adj = 0.05
    elif n >= 10 and perf >= 0.6: adj = 0.10
    elif n >= 5: adj = 0.15
    elif n >= 1: adj = 0.22
    else: adj = 0.30
    cost_acc = mem.cost_accuracy_score or 0.5
    if cost_acc < 0.5: adj += 0.10
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
        risk_score = round(1.0 - m.performance_score, 3)
        m.risk_level = "low" if risk_score < 0.2 else ("high" if risk_score > 0.5 else "medium")
        count += 1
    if count:
        db.flush()
        logger.info(f"Decayed {count} stale memories (>{days_threshold}d)")
    return {"decayed": count}