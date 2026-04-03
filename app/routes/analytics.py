"""Analytics API routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import ReportScheduleRequest, ReportScheduleResponse
from app.services import analytics_service, project_service
from app.utils.dependencies import require_user, can_access_project

router = APIRouter(prefix="/analytics", tags=["analytics"])


_PRIVILEGED_ROLES = {"admin", "manager", "buyer", "sourcing"}


def _require_scope(user: User, db: Session, project_id: Optional[str]) -> None:
    if project_id:
        project = project_service.get_project_by_id(db, project_id) or project_service.get_project_by_bom_id(db, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")
        return

    role = str(getattr(user, "role", "")).lower()
    if role not in _PRIVILEGED_ROLES and role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")


@router.get("/spend")
def spend(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_scope(user, db, project_id)
    return analytics_service.get_spend_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/vendors")
def vendors(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_scope(user, db, project_id)
    return analytics_service.get_vendor_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/categories")
def categories(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_scope(user, db, project_id)
    return analytics_service.get_category_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/trends")
def trends(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_scope(user, db, project_id)
    return analytics_service.get_trends(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/savings")
def savings(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_scope(user, db, project_id)
    return analytics_service.get_savings(db, project_id=project_id, start_date=start_date, end_date=end_date)