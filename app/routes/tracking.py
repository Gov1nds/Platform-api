"""Tracking routes — production tracking and execution feedback."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.schemas.rfq import TrackingResponse, FeedbackRequest
from app.utils.dependencies import require_user
from app.services import tracking_service

router = APIRouter(prefix="/tracking", tags=["tracking"])


@router.get("/rfq/{rfq_id}", response_model=List[TrackingResponse])
def get_tracking(rfq_id: str, db: Session = Depends(get_db)):
    """Get all tracking milestones for an RFQ."""
    entries = tracking_service.get_tracking(db, rfq_id)
    return [
        TrackingResponse(
            rfq_id=e.rfq_id, stage=e.stage,
            status_message=e.status_message, progress_percent=e.progress_percent,
        )
        for e in entries
    ]


@router.post("/rfq/{rfq_id}/start", response_model=TrackingResponse)
def start_production(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Initialize production tracking at T0."""
    entry = tracking_service.create_tracking(db, rfq_id)
    return TrackingResponse(
        rfq_id=entry.rfq_id, stage=entry.stage,
        status_message=entry.status_message, progress_percent=entry.progress_percent,
    )


@router.post("/rfq/{rfq_id}/advance", response_model=TrackingResponse)
def advance_stage(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Advance to next production stage."""
    entry = tracking_service.advance_stage(db, rfq_id, updated_by=user.email)
    if not entry:
        raise HTTPException(status_code=404, detail="Tracking not found")
    return TrackingResponse(
        rfq_id=entry.rfq_id, stage=entry.stage,
        status_message=entry.status_message, progress_percent=entry.progress_percent,
    )


@router.post("/rfq/{rfq_id}/feedback")
def submit_feedback(
    rfq_id: str,
    body: FeedbackRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Submit execution feedback — updates supplier learning memory."""
    try:
        fb = tracking_service.submit_feedback(
            db, rfq_id,
            actual_cost=body.actual_cost,
            actual_lead_time=body.actual_lead_time,
            feedback_notes=body.feedback_notes or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "rfq_id": rfq_id,
        "cost_delta": fb.cost_delta,
        "lead_time_delta": fb.lead_time_delta,
        "status": "feedback_recorded",
        "message": "Supplier memory updated based on feedback",
    }
