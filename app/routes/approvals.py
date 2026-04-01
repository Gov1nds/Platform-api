"""Approval routes — request listing, approve, reject, and creation."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.collaboration import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
)
from app.services import collaboration_service
from app.utils.dependencies import require_user

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
def list_approvals(
    project_id: str = Query(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        return collaboration_service.list_approvals(db, project_id, user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/{approval_id}")
def get_approval(
    approval_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        approval = collaboration_service.get_approval(db, approval_id, user)
        return collaboration_service.serialize_approval(db, approval)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("")
def create_approval(
    body: ApprovalCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        approval = collaboration_service.create_approval_request(
            db=db,
            user=user,
            project_id=body.project_id,
            title=body.title,
            description=body.description,
            required_role=body.required_role,
            thread_id=body.thread_id,
            rfq_batch_id=body.rfq_batch_id,
            vendor_id=body.vendor_id,
            assigned_to_user_id=body.assigned_to_user_id,
            due_at=body.due_at,
            metadata=body.metadata,
        )
        db.commit()
        return collaboration_service.serialize_approval(db, approval)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        approval = collaboration_service.approve_request(
            db=db,
            approval_id=approval_id,
            user=user,
            note=body.note,
            metadata=body.metadata,
        )
        db.commit()
        return collaboration_service.serialize_approval(db, approval)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        approval = collaboration_service.reject_request(
            db=db,
            approval_id=approval_id,
            user=user,
            note=body.note,
            metadata=body.metadata,
        )
        db.commit()
        return collaboration_service.serialize_approval(db, approval)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))