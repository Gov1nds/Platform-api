"""Project routes — dashboard, project detail, snapshot history, strategy run history.
FIXES:
  - get_project resolves BOTH project_id AND bom_id (fixes BOMAnalyzer URL bug)
  - Removed duplicate @router.get("")
  - NULL user_id projects rejected with 403
  - Snapshot history API (Item 2)
  - Strategy run history API (Item 1)
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.database import get_db
from app.schemas.project import ProjectDetail, ProjectSummary, StatusUpdate
from app.utils.dependencies import require_user
from app.models.user import User
from app.models.report_snapshot import ReportSnapshot
from app.models.strategy_run import StrategyRun
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


# ═══════════════════════════════════════════════════════════
# Report Snapshot History (Item 2)
# ═══════════════════════════════════════════════════════════

def _resolve_project_authed(project_id: str, user: User, db: Session):
    project = project_service.get_project_by_id(db, project_id)
    if not project:
        project = project_service.get_project_by_bom_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.user_id or project.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return project


@router.get("/{project_id}/snapshots")
def list_snapshots(
    project_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List all report snapshots for a project, newest first."""
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
    """Fetch a specific report snapshot by version with full JSON payloads."""
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
# Strategy Run History (Item 1)
# ═══════════════════════════════════════════════════════════

@router.get("/{project_id}/strategy-runs")
def list_strategy_runs(
    project_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List all strategy runs for a project, newest first."""
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
    """Fetch a specific strategy run with full JSON payloads."""
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