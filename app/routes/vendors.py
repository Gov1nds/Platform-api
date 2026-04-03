"""Vendor discovery routes."""
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Header
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
from app.services.workflow_service import begin_command, complete_command, fail_command
from app.services import project_service
from app.utils.dependencies import require_user, can_access_project, build_project_access_context

router = APIRouter(prefix="/vendors", tags=["vendors"])


def _project_accessible(project, user: User, db: Session = None) -> bool:
    if not project or not user:
        return False
    return can_access_project(user, project, db)


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
    if not _project_accessible(project, user, db):
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
    result["access"] = build_project_access_context(user, project, db)
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
    if project_id:
        project = vendor_service._project_context(db, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if not _project_accessible(project, user, db):
            raise HTTPException(status_code=403, detail="Not authorized")

    scorecard = vendor_service.build_vendor_scorecard(db, vendor_id, project_id=project_id)
    if not scorecard:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if project_id:
        scorecard["access"] = build_project_access_context(user, project, db)
    return scorecard


@router.post("/{vendor_id}/feedback")
def submit_vendor_feedback(
    vendor_id: str,
    body: VendorFeedbackRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    payload = body.model_dump(exclude_none=True)
    if body.project_id:
        project = vendor_service._project_context(db, body.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if not _project_accessible(project, user, db):
            raise HTTPException(status_code=403, detail="Not authorized")

    command, cached = begin_command(
        db,
        namespace="vendor.feedback",
        idempotency_key=idempotency_key,
        payload={
            "vendor_id": vendor_id,
            "payload": payload,
            "user_id": user.id,
        },
        request_method="POST",
        request_path=f"/api/v1/vendors/{vendor_id}/feedback",
        user_id=user.id,
        related_id=vendor_id,
    )
    if cached:
        return cached

    try:
        result = vendor_service.record_vendor_feedback(db, vendor_id, payload, user_id=user.id)
        if result.get("status") != "updated":
            raise HTTPException(status_code=400, detail="Feedback could not be recorded")
        if body.project_id:
            result["access"] = build_project_access_context(user, project, db)
        complete_command(db, command, result)
        db.commit()
        return result
    except HTTPException:
        fail_command(db, command, "Feedback could not be recorded")
        db.rollback()
        raise
    except Exception as exc:
        fail_command(db, command, str(exc))
        db.rollback()
        raise