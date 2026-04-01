"""Analytics API routes."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import ReportScheduleRequest, ReportScheduleResponse
from app.services import analytics_service
from app.utils.dependencies import require_roles

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/spend")
def spend(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    return analytics_service.get_spend_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/vendors")
def vendors(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    return analytics_service.get_vendor_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/categories")
def categories(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    return analytics_service.get_category_analytics(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/trends")
def trends(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    return analytics_service.get_trends(db, project_id=project_id, start_date=start_date, end_date=end_date)


@router.get("/savings")
def savings(
    project_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    return analytics_service.get_savings(db, project_id=project_id, start_date=start_date, end_date=end_date)