"""Project routes — control tower, lifecycle events, metrics, detail."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.report_snapshot import ReportSnapshot
from app.models.strategy_run import StrategyRun
from app.models.user import User, GuestSession
from app.schemas.project import (
    ProjectDetail,
    ProjectEventSchema,
    ProjectMetrics,
    ProjectSummary,
    StatusUpdate,
)
from app.services import project_service
from app.utils.dependencies import require_user, get_current_user, can_access_project, build_project_access_context

router = APIRouter(prefix="/projects", tags=["projects"])


def _guest_session_matches_project(db: Session, project, session_token: Optional[str]) -> bool:
    if not project or not session_token or not getattr(project, "guest_session_id", None):
        return False
    guest = (
        db.query(GuestSession)
        .filter(GuestSession.session_token == session_token)
        .first()
    )
    return bool(guest and str(guest.id) == str(project.guest_session_id))


@router.get("/metrics", response_model=ProjectMetrics)
def project_metrics(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return project_service.build_project_metrics(db, user.id)


@router.get("", response_model=list[ProjectSummary])
def list_projects(user: User = Depends(require_user), db: Session = Depends(get_db)):
    projects = project_service.list_projects_for_user(db, user.id)
    return [project_service.serialize_summary(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: str,
    session_token: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if user:
        if not can_access_project(user, project, db):
            raise HTTPException(status_code=403, detail="Not authorized")
        payload = project_service.serialize_detail(project)
        payload["access"] = build_project_access_context(user, project, db)
        payload["access"]["session_token_match"] = bool(
            session_token and (payload.get("analysis_lifecycle") or {}).get("session_token") == session_token
        )
        return payload

    if _guest_session_matches_project(db, project, session_token):
        payload = project_service.serialize_detail(project)
        preview = project_service.build_guest_preview(
            project,
            session_token=session_token,
            analysis_status=(project.project_metadata or {}).get("analysis_lifecycle", {}).get("analysis_status", "guest_preview"),
            report_visibility_level=(project.project_metadata or {}).get("analysis_lifecycle", {}).get("report_visibility_level", "preview"),
            unlock_status=(project.project_metadata or {}).get("analysis_lifecycle", {}).get("unlock_status", "locked"),
        )
        payload.update(preview)
        payload["access"] = build_project_access_context(user, project, db)
        return payload

    raise HTTPException(status_code=401, detail="Authentication required")


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project_status(project_id: str, status_update: StatusUpdate, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # H-3: Use collaborator-aware access instead of owner-only
    access = build_project_access_context(user, project, db)
    if not access.get("can_edit"):
        raise HTTPException(status_code=403, detail="Not authorized")

    old_status = project.workflow_stage or project.status
    new_status = project_service.normalize_project_stage(status_update.status, old_status)
    project.set_workflow_status(new_status)  # H-5: writes both status + workflow_stage
    project.project_metadata = project.project_metadata or {}
    project.project_metadata["workflow_stage"] = new_status
    project.project_metadata["next_action"] = project_service.project_stage_action(new_status)

    project_service.record_project_event(
        db,
        project,
        "manual_status_update",
        old_status,
        new_status,
        {"notes": status_update.notes},
        actor_user_id=user.id,
    )

    db.commit()
    db.refresh(project)
    payload = project_service.serialize_detail(project)
    payload["access"] = build_project_access_context(user, project, db)
    return payload


@router.get("/{project_id}/events", response_model=list[ProjectEventSchema])
def list_project_events(
    project_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_access_project(user, project, db):
        raise HTTPException(status_code=403, detail="Not authorized")

    events = project_service.list_project_events(db, project.id, limit=200)
    return [
        {
            "id": e.id,
            "project_id": e.project_id,
            "event_type": e.event_type,
            "old_status": e.old_status,
            "new_status": e.new_status,
            "payload": e.payload or {},
            "actor_user_id": e.actor_user_id,
            "created_at": e.created_at,
        }
        for e in events
    ]


# ═══════════════════════════════════════════════════════════
# Report Snapshot History
# ═══════════════════════════════════════════════════════════

def _resolve_project_authed(project_id: str, user: User, db: Session):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_access_project(user, project, db):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project


@router.get("/{project_id}/snapshots")
def list_snapshots(
    project_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_authed(project_id, user, db)
    snapshots = (
        db.query(ReportSnapshot)
        .filter(ReportSnapshot.project_id == project.id)
        .order_by(desc(ReportSnapshot.version))
        .all()
    )
    return [
        {
            "id": s.id,
            "version": s.version,
            "analyzer_version": s.analyzer_version,
            "classifier_version": s.classifier_version,
            "source_checksum": s.source_checksum,
            "total_parts": (s.replay_metadata or {}).get("total_parts", 0),
            "priority": (s.replay_metadata or {}).get("priority", "cost"),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in snapshots
    ]


@router.get("/{project_id}/snapshots/{version}")
def get_snapshot(
    project_id: str,
    version: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_authed(project_id, user, db)
    snapshot = (
        db.query(ReportSnapshot)
        .filter(
            ReportSnapshot.project_id == project.id,
            ReportSnapshot.version == version,
        )
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"Snapshot version {version} not found")
    return {
        "id": snapshot.id,
        "version": snapshot.version,
        "report_json": snapshot.report_json,
        "strategy_json": snapshot.strategy_json,
        "procurement_json": snapshot.procurement_json,
        "analyzer_version": snapshot.analyzer_version,
        "classifier_version": snapshot.classifier_version,
        "normalizer_version": snapshot.normalizer_version,
        "source_checksum": snapshot.source_checksum,
        "replay_metadata": snapshot.replay_metadata,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


# ═══════════════════════════════════════════════════════════
# Strategy Run History
# ═══════════════════════════════════════════════════════════

@router.get("/{project_id}/strategy-runs")
def list_strategy_runs(
    project_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_authed(project_id, user, db)
    runs = (
        db.query(StrategyRun)
        .filter(StrategyRun.project_id == project.id)
        .order_by(desc(StrategyRun.version))
        .all()
    )
    return [
        {
            "id": r.id,
            "version": r.version,
            "priority": r.priority,
            "target_currency": r.target_currency,
            "recommended_location": r.recommended_location,
            "average_cost": float(r.average_cost) if r.average_cost else None,
            "savings_percent": float(r.savings_percent) if r.savings_percent else None,
            "lead_time_days": float(r.lead_time_days) if r.lead_time_days else None,
            "decision_summary": r.decision_summary,
            "total_parts": r.total_parts,
            "is_current": str(r.id) == str(project.current_strategy_run_id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/{project_id}/strategy-runs/{run_id}")
def get_strategy_run(
    project_id: str,
    run_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_authed(project_id, user, db)
    run = (
        db.query(StrategyRun)
        .filter(
            StrategyRun.project_id == project.id,
            StrategyRun.id == run_id,
        )
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Strategy run not found")
    return {
        "id": run.id,
        "version": run.version,
        "priority": run.priority,
        "delivery_location": run.delivery_location,
        "target_currency": run.target_currency,
        "strategy_json": run.strategy_json,
        "procurement_json": run.procurement_json,
        "global_optimization": run.global_optimization,
        "region_distribution": run.region_distribution,
        "part_level_decisions": run.part_level_decisions,
        "recommended_location": run.recommended_location,
        "average_cost": float(run.average_cost) if run.average_cost else None,
        "savings_percent": float(run.savings_percent) if run.savings_percent else None,
        "lead_time_days": float(run.lead_time_days) if run.lead_time_days else None,
        "decision_summary": run.decision_summary,
        "total_parts": run.total_parts,
        "engine_version": run.engine_version,
        "is_current": str(run.id) == str(project.current_strategy_run_id),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }