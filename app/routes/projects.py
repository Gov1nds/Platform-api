from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.project import Project, ProjectEvent
from app.schemas import ProjectResponse, ProjectListResponse
from app.utils.dependencies import get_current_user, require_user, require_project_access, require_project_owner
from app.services.workflow.state_machine import transition_project, can_transition

router = APIRouter(prefix="/projects", tags=["Projects"])

def _serialize(p):
    d = ProjectResponse.model_validate(p).model_dump()
    d["events"] = [{"event_type":e.event_type,"old_status":e.old_status,"new_status":e.new_status,"created_at":str(e.created_at)} for e in (p.events or [])]
    return d

@router.get("", response_model=ProjectListResponse)
def list_projects(limit:int=Query(50,ge=1,le=200), offset:int=Query(0,ge=0),
                  user:User=Depends(require_user), db:Session=Depends(get_db)):
    q = db.query(Project).filter(Project.user_id == user.id).order_by(Project.created_at.desc())
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return ProjectListResponse(items=[ProjectResponse(**_serialize(p)) for p in items], total=total)

@router.get("/{project_id}")
def get_project(project_id:str, session_token:str=Query(""),
                user:User|None=Depends(get_current_user), db:Session=Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project: raise HTTPException(404,"Project not found")
    # ACL check
    from app.utils.dependencies import _check_project_access
    _check_project_access(db, project, user, session_token)
    return _serialize(project)

@router.patch("/{project_id}/status")
def update_status(project_id:str, new_status:str=Query(...),
                  user:User=Depends(require_user), db:Session=Depends(get_db)):
    project = require_project_owner(project_id, db, user)
    transition_project(db, project, new_status, actor_user_id=user.id)
    db.commit()
    return _serialize(project)

@router.patch("/{project_id}")
def update_project(project_id:str, body:dict, user:User=Depends(require_user), db:Session=Depends(get_db)):
    project = require_project_owner(project_id, db, user)
    for f in ("name","decision_summary"):
        if f in body: setattr(project, f, body[f])
    db.commit()
    return _serialize(project)
