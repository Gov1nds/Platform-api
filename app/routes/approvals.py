"""Approval routes — request listing, approve, reject, and creation."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.collaboration import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
)
from app.services import collaboration_service
from app.services.workflow_service import begin_command, complete_command, fail_command
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
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="approval.create",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
        request_method="POST",
        request_path="/api/v1/approvals",
        user_id=user.id,
        project_id=body.project_id,
        related_id=body.thread_id or body.rfq_batch_id or body.vendor_id or body.project_id,
    )
    if cached:
        return cached

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
        response = collaboration_service.serialize_approval(db, approval)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="approval.approve",
        idempotency_key=idempotency_key,
        payload={
            "approval_id": approval_id,
            "note": body.note,
            "metadata": body.metadata,
        },
        request_method="POST",
        request_path=f"/api/v1/approvals/{approval_id}/approve",
        user_id=user.id,
        related_id=approval_id,
    )
    if cached:
        return cached

    try:
        approval = collaboration_service.approve_request(
            db=db,
            approval_id=approval_id,
            user=user,
            note=body.note,
            metadata=body.metadata,
        )
        response = collaboration_service.serialize_approval(db, approval)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="approval.reject",
        idempotency_key=idempotency_key,
        payload={
            "approval_id": approval_id,
            "note": body.note,
            "metadata": body.metadata,
        },
        request_method="POST",
        request_path=f"/api/v1/approvals/{approval_id}/reject",
        user_id=user.id,
        related_id=approval_id,
    )
    if cached:
        return cached

    try:
        approval = collaboration_service.reject_request(
            db=db,
            approval_id=approval_id,
            user=user,
            note=body.note,
            metadata=body.metadata,
        )
        response = collaboration_service.serialize_approval(db, approval)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise