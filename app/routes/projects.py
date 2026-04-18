"""
Project management routes.

Endpoints:
  GET    /projects                         -- List projects (org-scoped, cursor pagination)
  POST   /projects                         -- Create project explicitly
  GET    /projects/{id}                    -- Get project detail
  PATCH  /projects/{id}                    -- Update project fields
  PATCH  /projects/{id}/status             -- Transition project status (SM-002)
  POST   /projects/{id}/archive            -- Archive project
  POST   /projects/{id}/cancel             -- Cancel project
  GET    /projects/{id}/weight-profile     -- Get weight profile
  PUT    /projects/{id}/weight-profile     -- Set weight profile
  POST   /projects/{id}/pipeline/run       -- Run Phase 1 runtime recommendation pipeline
  GET    /projects/{id}/recommendation     -- Get latest persisted recommendation

References: GAP-004 (SM-002), GAP-005 (org scoping),
            api-contract-review.md Section 5.2
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.enums import ProjectStatus
from app.models.bom import BOM
from app.models.project import Project, ProjectACL, ProjectEvent
from app.models.user import User
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectCursorResponse,
    ProjectResponse,
    WeightProfileRequest,
)
from app.schemas.recommendation import ProjectRecommendationResponse
from app.services.event_service import track
from app.services.runtime_pipeline import runtime_pipeline_service
from app.services.workflow.state_machine import transition_project, can_transition
from app.utils.dependencies import (
    get_current_user,
    require_org_scoped_project,
    require_project_access,
    require_project_owner,
    require_user,
    _check_project_access,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


# -- Serializer ---------------------------------------------------------------

def _serialize(p: Project) -> dict:
    d = ProjectResponse.model_validate(p).model_dump()
    d["events"] = [
        {
            "event_type": e.event_type,
            "old_status": e.old_status,
            "new_status": e.new_status,
            "created_at": str(e.created_at),
        }
        for e in (p.events or [])
    ]
    return d


# -- GET /projects -- org-scoped, cursor paginated ----------------------------

@router.get("", response_model=ProjectCursorResponse)
def list_projects(
    request: Request,
    status: str | None = Query(None, description="Filter by project status"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    org_id = getattr(request.state, "organization_id", None) or user.organization_id

    q = db.query(Project).filter(Project.deleted_at.is_(None))

    # Org scoping (GAP-005)
    if org_id:
        q = q.filter(Project.organization_id == org_id)
    else:
        q = q.filter(Project.user_id == user.id)

    if status:
        q = q.filter(Project.status == status.upper())

    total = q.count()

    # Cursor-based: use created_at desc, id desc
    if cursor:
        ref = db.query(Project).filter(Project.id == cursor).first()
        if ref:
            q = q.filter(
                (Project.created_at < ref.created_at)
                | ((Project.created_at == ref.created_at) & (Project.id < ref.id))
            )

    items = q.order_by(Project.created_at.desc(), Project.id.desc()).limit(limit).all()
    next_cursor = items[-1].id if len(items) == limit else None

    return ProjectCursorResponse(
        items=[ProjectResponse(**_serialize(p)) for p in items],
        next_cursor=next_cursor,
        total_count=total,
    )


# -- POST /projects -- explicit project creation ------------------------------

@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreateRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Create a new project explicitly (not via promote-to-project)."""
    org_id = getattr(request.state, "organization_id", None) or user.organization_id

    bom_id = body.bom_id
    if bom_id:
        bom = db.query(BOM).filter(BOM.id == bom_id).first()
        if not bom:
            raise HTTPException(404, "BOM not found")
    else:
        bom_id = None

    project = Project(
        bom_id=bom_id,
        user_id=user.id,
        organization_id=org_id,
        name=body.name,
        status=ProjectStatus.DRAFT,
        visibility="owner_only",
        weight_profile=body.weight_profile or "balanced",
        project_metadata={
            "delivery_location": body.delivery_location or "",
            "target_currency": body.target_currency or "USD",
        },
    )
    db.add(project)
    db.flush()

    # Grant owner ACL
    db.add(ProjectACL(
        project_id=project.id,
        principal_type="user",
        principal_id=user.id,
        role="owner",
        organization_id=org_id,
    ))

    track(db, "project_created", actor_id=user.id, resource_type="project", resource_id=project.id)
    db.commit()
    db.refresh(project)

    return ProjectResponse(**_serialize(project))


# -- GET /projects/{id} ------------------------------------------------------

@router.get("/{project_id}")
def get_project(
    project_id: str,
    request: Request,
    session_token: str = Query(""),
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.deleted_at.is_(None),
    ).first()
    if not project:
        raise HTTPException(404, "Project not found")

    _check_project_access(db, project, user, session_token)
    return _serialize(project)


# -- PATCH /projects/{id}/status -- SM-002 transition -------------------------

@router.patch("/{project_id}/status")
def update_status(
    project_id: str,
    request: Request,
    new_status: str = Query(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Transition project status via canonical SM-002 state machine."""
    project = require_org_scoped_project(project_id, request, db)

    # Ownership / role check
    if project.user_id != user.id and user.role not in ("admin", "BUYER_ADMIN", "ORGANIZATION_OWNER"):
        raise HTTPException(403, "Not the project owner")

    trace_id = getattr(request.state, "request_id", None)

    transition_project(
        db, project, new_status,
        actor_user_id=user.id,
        actor_type="USER",
        trace_id=trace_id,
    )
    db.commit()
    return _serialize(project)


# -- PATCH /projects/{id} -- update fields ------------------------------------

@router.patch("/{project_id}")
def update_project(
    project_id: str,
    body: dict,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = require_org_scoped_project(project_id, request, db)

    if project.user_id != user.id and user.role not in ("admin", "BUYER_ADMIN", "ORGANIZATION_OWNER"):
        raise HTTPException(403, "Not the project owner")

    allowed_fields = {"name", "decision_summary"}
    for f in allowed_fields:
        if f in body:
            setattr(project, f, body[f])

    db.commit()
    return _serialize(project)


# -- POST /projects/{id}/archive -- SM-002: CLOSED -> ARCHIVED ----------------

@router.post("/{project_id}/archive")
def archive_project(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Archive a project. Only valid from CLOSED state (SM-002)."""
    project = require_org_scoped_project(project_id, request, db)

    if project.status in (ProjectStatus.CANCELLED, ProjectStatus.ARCHIVED):
        raise HTTPException(409, f"Project is already {project.status}")

    if project.status != ProjectStatus.CLOSED:
        raise HTTPException(
            409,
            f"Cannot archive project from '{project.status}'; must be CLOSED first",
        )

    trace_id = getattr(request.state, "request_id", None)

    transition_project(
        db, project, ProjectStatus.ARCHIVED,
        actor_user_id=user.id,
        actor_type="USER",
        trace_id=trace_id,
    )

    track(db, "project_archived", actor_id=user.id, resource_type="project", resource_id=project.id)
    db.commit()
    return _serialize(project)


# -- POST /projects/{id}/cancel -- SM-002: any non-terminal -> CANCELLED ------

@router.post("/{project_id}/cancel")
def cancel_project(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Cancel a project. Does NOT cascade to POs (INV-11)."""
    project = require_org_scoped_project(project_id, request, db)

    if project.status in (ProjectStatus.CANCELLED, ProjectStatus.ARCHIVED, ProjectStatus.CLOSED):
        raise HTTPException(409, f"Project is already {project.status}")

    trace_id = getattr(request.state, "request_id", None)

    transition_project(
        db, project, ProjectStatus.CANCELLED,
        actor_user_id=user.id,
        actor_type="USER",
        trace_id=trace_id,
    )

    track(db, "project_cancelled", actor_id=user.id, resource_type="project", resource_id=project.id)
    db.commit()
    return _serialize(project)


# -- GET /projects/{id}/weight-profile ----------------------------------------

@router.get("/{project_id}/weight-profile")
def get_weight_profile(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = require_org_scoped_project(project_id, request, db)
    return {"weight_profile": project.weight_profile}


# -- PUT /projects/{id}/weight-profile ----------------------------------------

@router.put("/{project_id}/weight-profile")
def set_weight_profile(
    project_id: str,
    body: WeightProfileRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = require_org_scoped_project(project_id, request, db)

    valid = {"speed_first", "cost_first", "quality_first", "balanced"}
    if body.weight_profile not in valid:
        raise HTTPException(400, f"Invalid weight profile. Must be one of: {valid}")

    project.weight_profile = body.weight_profile
    db.commit()
    return {"weight_profile": project.weight_profile}


# -- POST /projects/{id}/pipeline/run -----------------------------------------

@router.post("/{project_id}/pipeline/run", response_model=ProjectRecommendationResponse)
def run_project_pipeline(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Run the Phase 1 persisted procurement recommendation pipeline.

    Flow:
    raw BOM -> analyzer normalization -> seeded vendor enrichment ->
    live FX + freight baseline -> scoring -> persisted recommendation
    """
    project = require_org_scoped_project(project_id, request, db)

    if project.user_id != user.id and user.role not in ("admin", "BUYER_ADMIN", "ORGANIZATION_OWNER"):
        # Also allow explicit ACL access for collaborators.
        _check_project_access(db, project, user, None, require_role="editor")

    try:
        recommendation = runtime_pipeline_service.run_project_pipeline(
            db,
            project=project,
            actor_id=user.id,
            trace_id=getattr(request.state, "request_id", None),
        )
        return recommendation
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Phase 1 runtime pipeline failed for project %s", project_id)
        raise HTTPException(500, "Failed to generate Phase 1 recommendation") from exc


# -- GET /projects/{id}/recommendation ----------------------------------------

@router.get("/{project_id}/recommendation", response_model=ProjectRecommendationResponse)
def get_latest_recommendation(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Return the latest persisted recommendation snapshot for the project.
    """
    project = require_org_scoped_project(project_id, request, db)
    _check_project_access(db, project, user, None)

    recommendation = runtime_pipeline_service.get_latest_recommendation(
        db,
        project_id=project.id,
    )
    if recommendation is None:
        raise HTTPException(404, "No recommendation snapshot found for this project")

    return recommendation

# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Vendor Intelligence recommendation endpoint (additive).
#
# The legacy /projects/{id}/recommendation endpoint is unchanged.
# This new endpoint returns the Phase-3 VendorIntelligenceRecommendationResponse
# with three-strategy output, geo context, landed cost, safety report.
# ═════════════════════════════════════════════════════════════════════════════

from app.schemas.recommendation import (
    VendorIntelligenceRecommendationResponse as _VendorIntelRecResp,
)


@router.post(
    "/{project_id}/intelligence-recommendation",
    response_model=_VendorIntelRecResp,
)
def run_vendor_intelligence_recommendation(
    project_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Run the Phase 3 vendor-intelligence recommendation pipeline.

    Produces three named sourcing strategies (Fastest Local / Best Domestic
    Value / Lowest Landed Cost), a decision-safety report, geo bucket context,
    commodity market signals, and per-vendor landed-cost breakdowns.
    """
    project = require_org_scoped_project(project_id, request, db)

    if project.user_id != user.id and user.role not in ("admin", "BUYER_ADMIN", "ORGANIZATION_OWNER"):
        _check_project_access(db, project, user, None, require_role="editor")

    try:
        return runtime_pipeline_service.generate_vendor_intelligence_recommendation(
            db,
            project=project,
            actor_id=user.id,
            trace_id=getattr(request.state, "request_id", None),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Phase 3 intelligence recommendation failed for project %s", project_id)
        raise HTTPException(500, "Failed to generate intelligence recommendation") from exc


@router.get("/{project_id}/consolidation")
def consolidation(project_id: str, user = Depends(require_user), db: Session = Depends(get_db)):
    """Task 14: Vendor consolidation analysis."""
    from app.services.consolidation_service import analyze_consolidation
    return analyze_consolidation(db, project_id)
