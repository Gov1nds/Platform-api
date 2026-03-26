"""Tracking Service — FIXED: predicted lead time from analysis, not hardcoded."""
import logging
from typing import Optional, List
from sqlalchemy.orm import Session

from app.models.tracking import ProductionTracking, ExecutionFeedback, TrackingStage
from app.models.rfq import RFQ, RFQStatus
from app.models.analysis import AnalysisResult
from app.models.memory import SupplierMemory
from app.services import project_service

logger = logging.getLogger("tracking_service")

STAGE_PROGRESS = {
    TrackingStage.T0.value: 0,
    TrackingStage.T1.value: 25,
    TrackingStage.T2.value: 50,
    TrackingStage.T3.value: 75,
    TrackingStage.T4.value: 100,
}

STAGE_MESSAGES = {
    TrackingStage.T0.value: "Order placed — awaiting confirmation",
    TrackingStage.T1.value: "Material procurement in progress",
    TrackingStage.T2.value: "Manufacturing started",
    TrackingStage.T3.value: "Quality check / inspection",
    TrackingStage.T4.value: "Shipped — delivery in transit",
}


def create_tracking(db: Session, rfq_id: str) -> ProductionTracking:
    tracking = ProductionTracking(
        rfq_id=rfq_id,
        stage=TrackingStage.T0.value,
        status_message=STAGE_MESSAGES[TrackingStage.T0.value],
        progress_percent=0,
    )
    db.add(tracking)
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if rfq:
        rfq.status = RFQStatus.in_production.value
    db.flush()
    db.refresh(tracking)
    return tracking


def advance_stage(db: Session, rfq_id: str, updated_by: str = "system") -> Optional[ProductionTracking]:
    tracking = (
        db.query(ProductionTracking)
        .filter(ProductionTracking.rfq_id == rfq_id)
        .order_by(ProductionTracking.created_at.desc())
        .first()
    )
    if not tracking:
        return create_tracking(db, rfq_id)

    stages = list(STAGE_PROGRESS.keys())
    current_idx = stages.index(tracking.stage) if tracking.stage in stages else 0

    if current_idx >= len(stages) - 1:
        rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
        if rfq:
            rfq.status = RFQStatus.completed.value
            project_service.sync_project_completion(db, rfq)
        return tracking

    next_stage = stages[current_idx + 1]
    new_tracking = ProductionTracking(
        rfq_id=rfq_id,
        stage=next_stage,
        status_message=STAGE_MESSAGES.get(next_stage, ""),
        progress_percent=STAGE_PROGRESS.get(next_stage, 0),
        updated_by=updated_by,
    )
    db.add(new_tracking)
    db.flush()
    db.refresh(new_tracking)
    return new_tracking


def get_tracking(db: Session, rfq_id: str) -> List[ProductionTracking]:
    return (
        db.query(ProductionTracking)
        .filter(ProductionTracking.rfq_id == rfq_id)
        .order_by(ProductionTracking.created_at.asc())
        .all()
    )


def _get_predicted_lead_time(db: Session, rfq_id: str) -> float:
    """FIXED: Get predicted lead time from analysis/strategy, not hardcoded 14."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if rfq and rfq.bom_id:
        analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == rfq.bom_id).first()
        if analysis and analysis.lead_time:
            return float(analysis.lead_time)
        if analysis and analysis.strategy_output:
            rec = analysis.strategy_output.get("recommended_strategy", {})
            if rec.get("lead_time"):
                return float(rec["lead_time"])
    return 14.0  # fallback only if no analysis data exists


def submit_feedback(
    db: Session,
    rfq_id: str,
    actual_cost: Optional[float] = None,
    actual_lead_time: Optional[float] = None,
    feedback_notes: str = "",
) -> ExecutionFeedback:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    predicted_cost = rfq.total_estimated_cost or 0
    # FIXED: get predicted lead from analysis, not hardcoded 14
    predicted_lead = _get_predicted_lead_time(db, rfq_id)

    cost_delta = (actual_cost - predicted_cost) if actual_cost is not None else None
    lead_delta = (actual_lead_time - predicted_lead) if actual_lead_time is not None else None

    fb = ExecutionFeedback(
        rfq_id=rfq_id,
        predicted_cost=predicted_cost,
        actual_cost=actual_cost,
        cost_delta=cost_delta,
        predicted_lead_time=predicted_lead,
        actual_lead_time=actual_lead_time,
        lead_time_delta=lead_delta,
        feedback_notes=feedback_notes,
    )
    db.add(fb)

    if rfq.selected_vendor_id:
        _update_memory(db, rfq.selected_vendor_id, cost_delta, lead_delta)

    db.flush()
    db.refresh(fb)
    logger.info("Feedback recorded for RFQ %s: cost_delta=%s, lead_delta=%s", rfq_id, cost_delta, lead_delta)
    return fb


def get_rfq_for_feedback(db: Session, rfq_id: str) -> Optional[RFQ]:
    return db.query(RFQ).filter(RFQ.id == rfq_id).first()


def _update_memory(db: Session, vendor_id: str, cost_delta: Optional[float], lead_delta: Optional[float]):
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return

    mem.total_orders = (mem.total_orders or 0) + 1
    n = mem.total_orders

    if cost_delta is not None:
        cost_pct = cost_delta / max(abs(cost_delta) + 100, 1) * 100
        mem.avg_cost_delta_pct = ((mem.avg_cost_delta_pct or 0) * (n - 1) + cost_pct) / n
        mem.cost_accuracy_score = max(0, min(1, 1.0 - abs(mem.avg_cost_delta_pct) / 50))

    if lead_delta is not None:
        mem.avg_lead_delta_days = ((mem.avg_lead_delta_days or 0) * (n - 1) + lead_delta) / n
        mem.delivery_accuracy_score = max(0, min(1, 1.0 - abs(mem.avg_lead_delta_days) / 14))

    mem.performance_score = (mem.cost_accuracy_score + mem.delivery_accuracy_score) / 2
    mem.risk_level = max(0, min(1, 1.0 - mem.performance_score))