"""Scheduled report routes."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import ReportScheduleRequest, ReportScheduleResponse
from app.services import analytics_service
from app.utils.dependencies import require_roles

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/schedule", response_model=ReportScheduleResponse)
def schedule_report(
    body: ReportScheduleRequest,
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    row = analytics_service.schedule_report(
        db,
        report_name=body.report_name,
        report_type=body.report_type,
        frequency=body.frequency,
        recipients_json=body.recipients_json,
        filters_json=body.filters_json,
        is_active=body.is_active,
        next_run_at=body.next_run_at,
        metadata=body.metadata,
    )
    db.commit()
    return row