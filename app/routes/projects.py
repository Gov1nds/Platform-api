"""Project routes — dashboard and project detail APIs.
FIXES:
  - get_project resolves BOTH project_id AND bom_id (fixes BOMAnalyzer URL bug)
  - Removed duplicate @router.get("")
  - NULL user_id projects rejected with 403
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.project import ProjectDetail, ProjectSummary, StatusUpdate
from app.utils.dependencies import require_user
from app.models.user import User
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(user: User = Depends(require_user), db: Session = Depends(get_db)):
    projects = project_service.list_projects_for_user(db, user.id)
    return [project_service.serialize_summary(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    # FIXED: Try project_id first, then fall back to bom_id lookup
    # This fixes the BOMAnalyzer.jsx bug where frontend sends bomId instead of project.id
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # FIXED: reject NULL user_id projects
    if not project.user_id or project.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return project_service.serialize_detail(project)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project_status(project_id: str, status_update: StatusUpdate, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.user_id or project.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    project.status = status_update.status or project.status
    db.commit()
    db.refresh(project)
    return project_service.serialize_detail(project)