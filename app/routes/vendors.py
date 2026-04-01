"""Vendor discovery routes."""
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.vendor import (
    VendorProfileSchema,
    VendorMatchRunSchema,
    VendorScorecardSchema,
    VendorFeedbackRequest,
)
from app.services import vendor_service
from app.utils.dependencies import require_user

router = APIRouter(prefix="/vendors", tags=["vendors"])


def _project_accessible(project, user: User) -> bool:
    return bool(project and project.user_id and project.user_id == user.id)


@router.get("/match", response_model=VendorMatchRunSchema)
def match_vendors(
    project_id: str = Query(...),
    regions: Optional[str] = Query(None),
    certifications: Optional[str] = Query(None),
    max_moq: Optional[float] = Query(None),
    max_lead_time: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = vendor_service._project_context(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not _project_accessible(project, user):
        raise HTTPException(status_code=403, detail="Not authorized")

    filters: Dict[str, Any] = {
        "regions": regions,
        "certifications": certifications,
        "max_moq": max_moq,
        "max_lead_time": max_lead_time,
        "max_price": max_price,
        "search": search,
        "delivery_region": project.recommended_location or "",
        "currency": (project.project_metadata or {}).get("currency", "USD"),
    }

    result = vendor_service.match_vendors_for_project(
        db=db,
        project_id=project_id,
        user_id=user.id,
        filters=filters,
        limit=limit,
    )
    return result


@router.get("/{vendor_id}", response_model=VendorProfileSchema)
def get_vendor(vendor_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    vendor = vendor_service.get_vendor_profile(db, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


@router.get("/{vendor_id}/scorecard", response_model=VendorScorecardSchema)
def get_vendor_scorecard(
    vendor_id: str,
    project_id: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    scorecard = vendor_service.build_vendor_scorecard(db, vendor_id, project_id=project_id)
    if not scorecard:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return scorecard


@router.post("/{vendor_id}/feedback")
def submit_vendor_feedback(
    vendor_id: str,
    body: VendorFeedbackRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_none=True)
    result = vendor_service.record_vendor_feedback(db, vendor_id, payload, user_id=user.id)
    if result.get("status") != "updated":
        raise HTTPException(status_code=400, detail="Feedback could not be recorded")
    return result