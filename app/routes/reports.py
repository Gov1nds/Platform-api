"""Scheduled report routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import ReportScheduleRequest, ReportScheduleResponse
from app.services import analytics_service, project_service
from app.utils.dependencies import require_user, can_access_project

router = APIRouter(prefix="/reports", tags=["reports"])


_PRIVILEGED_ROLES = {"admin", "manager", "buyer", "sourcing"}


def _scope_from_filters(user: User, db: Session, body: ReportScheduleRequest) -> None:
    project_id = (body.filters_json or {}).get("project_id")
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


@router.post("/schedule", response_model=ReportScheduleResponse)
def schedule_report(
    body: ReportScheduleRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _scope_from_filters(user, db, body)
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
