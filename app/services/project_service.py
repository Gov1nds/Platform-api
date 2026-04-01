"""Project service — canonical project projection and reporting adapters."""
from __future__ import annotations

# Standard library
import copy
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# Third-party
from sqlalchemy.orm import Session

# Application
from app.models.analysis import AnalysisResult
from app.models.bom import BOM
from app.models.project import Project, ProjectEvent
from app.models.rfq import RFQBatch as RFQ, RFQStatus
from app.models.tracking import ProductionTracking, TrackingStage
from app.models.report_snapshot import ReportSnapshot
from app.models.strategy_run import StrategyRun

logger = logging.getLogger("project_service")


STATUS_ORDER = {
    "uploaded": 0,
    "analyzed": 1,
    "quoting": 2,
    "quoted": 3,
    "approved": 4,
    "in_production": 5,
    "qc_inspection": 6,
    "shipped": 7,
    "completed": 8,
}

from datetime import datetime

PROJECT_WORKFLOW_STAGES = {
    "draft",
    "guest_preview",
    "project_hydrated",
    "strategy",
    "vendor_match",
    "rfq_pending",
    "rfq_sent",
    "quote_compare",
    "negotiation",
    "vendor_selected",
    "po_issued",
    "in_production",
    "qc_inspection",
    "shipped",
    "delivered",
    "spend_recorded",
    "completed",
    "cancelled",
    "error",
}

PROJECT_STATUS_ALIASES = {
    "uploaded": "draft",
    "preview": "guest_preview",
    "analyzed": "project_hydrated",
    "ready": "project_hydrated",
    "rfq_pending": "rfq_pending",
    "quoting": "rfq_sent",
    "quoted": "quote_compare",
    "approved": "vendor_selected",
    "rejected": "project_hydrated",
    "closed": "completed",
    "in_production": "in_production",
    "qc_inspection": "qc_inspection",
    "shipped": "shipped",
    "delivered": "delivered",
    "completed": "completed",
}

PROJECT_STAGE_ACTIONS = {
    "draft": "Upload BOM",
    "guest_preview": "Sign in to unlock full report",
    "project_hydrated": "Review strategy",
    "strategy": "Review sourcing strategy",
    "vendor_match": "Shortlist vendors",
    "rfq_pending": "Send RFQ",
    "rfq_sent": "Collect quotes",
    "quote_compare": "Compare quotes",
    "negotiation": "Negotiate terms",
    "vendor_selected": "Issue PO",
    "po_issued": "Track production",
    "in_production": "Monitor build progress",
    "qc_inspection": "Review quality check",
    "shipped": "Track shipment",
    "delivered": "Confirm delivery",
    "spend_recorded": "Review spend analytics",
    "completed": "Closed",
    "cancelled": "Cancelled",
    "error": "Needs attention",
}


def normalize_project_stage(value: Optional[str], default: str = "draft") -> str:
    if not value:
        return default
    v = str(value).strip().lower()
    return PROJECT_STATUS_ALIASES.get(v, v) if (PROJECT_STATUS_ALIASES.get(v, v) in PROJECT_WORKFLOW_STAGES) else default


def project_stage_action(stage: Optional[str]) -> str:
    return PROJECT_STAGE_ACTIONS.get(normalize_project_stage(stage), "Review project")


def record_project_event(
    db: Session,
    project: Project,
    event_type: str,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    actor_user_id: Optional[str] = None,
):
    return _emit_event(
        db=db,
        project=project,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        payload=payload or {},
        actor_user_id=actor_user_id,
    )

ANALYSIS_LIFECYCLE_DEFAULTS = {
    "analysis_status": "guest_preview",
    "report_visibility_level": "preview",
    "unlock_status": "locked",
}


def _analysis_lifecycle_payload(
    bom: BOM,
    project: Optional[Project],
    session_token: Optional[str],
    analysis_status: str,
    report_visibility_level: str,
    unlock_status: str,
) -> Dict[str, Any]:
    return {
        "guest_bom_id": str(bom.id) if bom and bom.id else None,
        "project_id": str(project.id) if project and project.id else None,
        "session_token": session_token or "",
        "analysis_status": analysis_status,
        "report_visibility_level": report_visibility_level,
        "unlock_status": unlock_status,
        "workspace_route": f"/project/{project.id}" if project and project.id else None,
    }


def persist_analysis_lifecycle(
    db: Session,
    bom: BOM,
    analysis: AnalysisResult,
    project: Project,
    *,
    session_token: Optional[str] = None,
    analysis_status: str = "guest_preview",
    report_visibility_level: str = "preview",
    unlock_status: str = "locked",
) -> Dict[str, Any]:
    """
    Persist the BOM preview/unlock lifecycle into existing JSONB columns
    without requiring a breaking schema migration.
    """
    lifecycle = _analysis_lifecycle_payload(
        bom=bom,
        project=project,
        session_token=session_token,
        analysis_status=analysis_status,
        report_visibility_level=report_visibility_level,
        unlock_status=unlock_status,
    )

    bom.model_metadata = copy.deepcopy(bom.model_metadata or {})
    bom.model_metadata["analysis_lifecycle"] = lifecycle
    bom.model_metadata["analysis_status"] = analysis_status
    bom.model_metadata["report_visibility_level"] = report_visibility_level
    bom.model_metadata["unlock_status"] = unlock_status

    analysis.structured_output = copy.deepcopy(analysis.structured_output or {})
    analysis.structured_output["analysis_lifecycle"] = lifecycle
    analysis.project_id = project.id

    project.project_metadata = copy.deepcopy(project.project_metadata or {})
    project.project_metadata["analysis_lifecycle"] = lifecycle
    project.project_metadata["analysis_status"] = analysis_status
    project.project_metadata["report_visibility_level"] = report_visibility_level
    project.project_metadata["unlock_status"] = unlock_status
    project.visibility = report_visibility_level

    bom.project_id = project.id

    db.flush()
    return lifecycle

def _emit_event(db: Session, project: Project, event_type: str,
                old_status: Optional[str] = None, new_status: Optional[str] = None,
                payload: Optional[Dict] = None, actor_user_id: Optional[str] = None):
    """Write a ProjectEvent for audit trail."""
    try:
        db.add(ProjectEvent(
            project_id=project.id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            payload=payload or {},
            actor_user_id=actor_user_id or project.user_id,
        ))
        db.flush()
    except Exception as e:
        logger.warning(f"Failed to emit project event: {e}")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _project_status_from_tracking(stage: Optional[str], current: Optional[str] = None) -> Optional[str]:
    if not stage:
        return current
    s = str(stage).strip().lower()
    mapping = {
        TrackingStage.T0.value.lower(): "in_production",
        TrackingStage.T1.value.lower(): "in_production",
        TrackingStage.T2.value.lower(): "in_production",
        TrackingStage.T3.value.lower(): "qc_inspection",
        TrackingStage.T4.value.lower(): "shipped",
        "delivered": "delivered",
        "receipt_confirmed": "delivered",
        "received": "delivered",
        "closed": "completed",
    }
    return mapping.get(s, current)


def _project_status_from_rfq(status: Optional[str], current: Optional[str] = None) -> Optional[str]:
    if not status:
        return current
    s = normalize_project_stage(status, current or "project_hydrated")
    mapping = {
        "draft": "rfq_pending",
        "rfq_pending": "rfq_pending",
        "sent": "rfq_sent",
        "rfq_sent": "rfq_sent",
        "partial": "quote_compare",
        "quoted": "quote_compare",
        "quote_compare": "quote_compare",
        "approved": "vendor_selected",
        "vendor_selected": "vendor_selected",
        "rejected": "project_hydrated",
        "closed": "completed",
        "completed": "completed",
        "error": "error",
        "in_production": "in_production",
    }
    return mapping.get(s, current)


def resolve_bom_id(db: Session, bom_or_project_id: str) -> Optional[str]:
    project = db.query(Project).filter(Project.id == bom_or_project_id).first()
    if project:
        return project.bom_id
    bom = db.query(BOM).filter(BOM.id == bom_or_project_id).first()
    if bom:
        return bom.id
    return None


def get_project_by_bom_id(db: Session, bom_id: str) -> Optional[Project]:
    return db.query(Project).filter(Project.bom_id == bom_id).first()


def get_project_by_id(db: Session, project_id: str) -> Optional[Project]:
    return db.query(Project).filter(Project.id == project_id).first()


def upsert_project_from_analysis(
    db: Session,
    bom: BOM,
    analysis: AnalysisResult,
    analyzer_output: Dict[str, Any],
    strategy: Dict[str, Any],
    procurement: Dict[str, Any],
) -> Project:
    project = get_project_by_bom_id(db, bom.id)
    is_new = project is None
    if not project:
        project = Project(bom_id=bom.id)
        db.add(project)

    cost_summary = procurement.get("cost_summary", {}) or strategy.get("procurement_strategy", {}).get("cost_summary", {}) or {}
    rec = strategy.get("recommended_strategy", {}) or {}
    timeline = procurement.get("timeline", {}) or strategy.get("procurement_strategy", {}).get("timeline", {}) or {}
    risk = procurement.get("risk_analysis", {}) or strategy.get("procurement_strategy", {}).get("risk_analysis", {}) or {}

    project.user_id = bom.user_id or analysis.user_id
    project.guest_session_id = bom.guest_session_id
    project.name = bom.name or bom.file_name or "Uploaded BOM"
    project.file_name = bom.file_name

    # Canonical workflow fields
    project.workflow_stage = normalize_project_stage(project.workflow_stage or "project_hydrated", "project_hydrated")
    project.status = project.workflow_stage
    project.visibility_level = project.visibility_level or ("full" if project.user_id else "preview")
    project.visibility = project.visibility_level

    project.total_parts = bom.total_parts or len((analyzer_output or {}).get("components", []) or [])
    project.recommended_location = rec.get("location") or analysis.recommended_location
    project.average_cost = _as_float(cost_summary.get("average"), _as_float(analysis.average_cost))
    if cost_summary.get("range") and len(cost_summary.get("range")) >= 2:
        project.cost_range_low = _as_float(cost_summary.get("range")[0], _as_float(analysis.cost_range_low))
        project.cost_range_high = _as_float(cost_summary.get("range")[1], _as_float(analysis.cost_range_high))
    else:
        project.cost_range_low = _as_float(analysis.cost_range_low)
        project.cost_range_high = _as_float(analysis.cost_range_high)
    project.savings_percent = _as_float(cost_summary.get("savings_percent"), _as_float(analysis.savings_percent))
    project.lead_time = _as_float(rec.get("lead_time"), _as_float(analysis.lead_time))
    project.decision_summary = strategy.get("decision_summary") or analysis.decision_summary
    project.analyzer_report = build_canonical_report(analyzer_output, strategy, procurement, bom, analysis)
    project.strategy = copy.deepcopy(strategy)
    project.procurement_plan = copy.deepcopy(procurement)

    # Canonical pointers
    project.current_analysis_id = analysis.id

    # Versioning
    project.latest_report_version = (project.latest_report_version or 0) + 1
    project.latest_strategy_version = (project.latest_strategy_version or 0) + 1

    project.project_metadata = {
        "category_summary": (analyzer_output or {}).get("summary", {}).get("categories", {}),
        "analysis_id": analysis.id,
        "risk_level": risk.get("overall_risk_level") or risk.get("risk_level"),
        "currency": strategy.get("currency") or procurement.get("currency") or "USD",
        "workflow_stage": project.workflow_stage,
        "visibility_level": project.visibility_level,
        "next_action": project_stage_action(project.workflow_stage),
    }

    db.flush()

    # Durable report snapshot
    try:
        raw_meta = (analyzer_output or {}).get("_meta", {})
        db.add(ReportSnapshot(
            project_id=project.id,
            version=project.latest_report_version,
            report_json=project.analyzer_report,
            strategy_json=copy.deepcopy(strategy),
            procurement_json=copy.deepcopy(procurement),
            analyzer_version=raw_meta.get("version", "unknown"),
            classifier_version=raw_meta.get("version", "unknown"),
            normalizer_version=raw_meta.get("normalizer_version", "unknown"),
            source_checksum=raw_meta.get("file_checksum"),
            replay_metadata={
                "bom_id": str(bom.id),
                "analysis_id": str(analysis.id),
                "total_parts": project.total_parts,
                "priority": strategy.get("bom_summary", {}).get("priority", "cost"),
            },
        ))
        db.flush()
    except Exception as e:
        logger.warning(f"ReportSnapshot save failed (non-fatal): {e}")

    # Strategy run
    try:
        strat_run = StrategyRun(
            project_id=project.id,
            analysis_id=analysis.id,
            version=project.latest_strategy_version,
            priority=strategy.get("bom_summary", {}).get("priority", "cost"),
            delivery_location=strategy.get("bom_summary", {}).get("delivery_location", ""),
            target_currency=strategy.get("currency") or procurement.get("currency") or "USD",
            strategy_json=copy.deepcopy(strategy),
            procurement_json=copy.deepcopy(procurement),
            global_optimization=strategy.get("global_optimization", {}),
            region_distribution=strategy.get("region_distribution", {}),
            part_level_decisions=strategy.get("part_level_decisions", []),
            recommended_location=rec.get("location", ""),
            average_cost=_as_float(cost_summary.get("average"), 0),
            savings_percent=_as_float(cost_summary.get("savings_percent"), 0),
            lead_time_days=_as_float(rec.get("lead_time"), 0),
            decision_summary=strategy.get("decision_summary", ""),
            total_parts=project.total_parts or 0,
            engine_version=(analyzer_output or {}).get("_meta", {}).get("version", "unknown"),
        )
        db.add(strat_run)
        db.flush()
        project.current_strategy_run_id = strat_run.id
        db.flush()
    except Exception as e:
        logger.warning(f"StrategyRun save failed (non-fatal): {e}")

    if is_new:
        record_project_event(
            db,
            project,
            "project_created",
            None,
            project.workflow_stage,
            {"total_parts": project.total_parts, "source": "bom_upload"},
        )
    else:
        record_project_event(
            db,
            project,
            "project_reanalyzed",
            project.status,
            project.workflow_stage,
            {"total_parts": project.total_parts},
        )

    return project


def update_project_status_from_rfq(db: Session, rfq: RFQ) -> Optional[Project]:
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None

    old_status = project.workflow_stage or project.status
    project.rfq_status = rfq.status
    project.current_rfq_id = rfq.id

    new_status = _project_status_from_rfq(rfq.status, project.workflow_stage or project.status)
    if new_status:
        project.workflow_stage = normalize_project_stage(new_status, project.workflow_stage or "project_hydrated")
        project.status = project.workflow_stage

    project.project_metadata = copy.deepcopy(project.project_metadata or {})
    project.project_metadata["rfq_status"] = rfq.status
    project.project_metadata["workflow_stage"] = project.workflow_stage
    project.project_metadata["next_action"] = project_stage_action(project.workflow_stage)

    if project.workflow_stage != old_status:
        record_project_event(
            db,
            project,
            "rfq_status_change",
            old_status,
            project.workflow_stage,
            {"rfq_id": rfq.id, "rfq_status": rfq.status},
        )

    db.flush()
    return project


def update_project_status_from_tracking(db: Session, rfq_id: str, stage: Optional[str] = None) -> Optional[Project]:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None

    old_status = project.workflow_stage or project.status
    project.tracking_stage = stage or project.tracking_stage

    new_status = _project_status_from_tracking(stage, project.workflow_stage or project.status)
    if new_status:
        project.workflow_stage = normalize_project_stage(new_status, project.workflow_stage or "in_production")
        project.status = project.workflow_stage

    project.project_metadata = copy.deepcopy(project.project_metadata or {})
    project.project_metadata["tracking_stage"] = project.tracking_stage
    project.project_metadata["workflow_stage"] = project.workflow_stage
    project.project_metadata["next_action"] = project_stage_action(project.workflow_stage)

    if project.workflow_stage != old_status:
        record_project_event(
            db,
            project,
            "tracking_stage_change",
            old_status,
            project.workflow_stage,
            {"rfq_id": rfq_id, "stage": stage},
        )

    db.flush()
    return project


def sync_project_completion(db: Session, rfq: RFQ) -> Optional[Project]:
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None

    old_status = project.workflow_stage or project.status
    project.workflow_stage = "completed"
    project.status = "completed"
    project.rfq_status = rfq.status
    project.visibility_level = project.visibility_level or "full"
    project.project_metadata = copy.deepcopy(project.project_metadata or {})
    project.project_metadata["workflow_stage"] = "completed"
    project.project_metadata["next_action"] = project_stage_action("completed")

    record_project_event(
        db,
        project,
        "project_completed",
        old_status,
        "completed",
        {"rfq_id": rfq.id},
    )

    db.flush()
    return project


def list_projects_for_user(db: Session, user_id: str) -> List[Project]:
    return (
        db.query(Project)
        .filter(Project.user_id == user_id)
        .order_by(Project.created_at.desc())
        .all()
    )


def serialize_summary(project: Project) -> Dict[str, Any]:
    lifecycle = (project.project_metadata or {}).get("analysis_lifecycle", {}) or {}
    workflow_stage = normalize_project_stage(project.workflow_stage or project.status, "draft")
    return {
        "project_id": project.id,
        "name": project.name,
        "status": workflow_stage,
        "workflow_stage": workflow_stage,
        "visibility_level": project.visibility_level or project.visibility or "private",
        "visibility": project.visibility or project.visibility_level or "private",
        "total_parts": project.total_parts or 0,
        "created_at": project.created_at,
        "cost": project.average_cost,
        "savings_percent": project.savings_percent,
        "lead_time": project.lead_time,
        "file_name": project.file_name,
        "recommended_location": project.recommended_location,
        "currency": project.currency or "USD",
        "rfq_status": project.rfq_status or "none",
        "tracking_stage": project.tracking_stage or "init",
        "current_vendor_match_id": project.current_vendor_match_id,
        "current_quote_id": project.current_quote_id,
        "current_po_id": project.current_po_id,
        "current_shipment_id": project.current_shipment_id,
        "current_invoice_id": project.current_invoice_id,
        "analysis_status": lifecycle.get("analysis_status"),
        "report_visibility_level": lifecycle.get("report_visibility_level"),
        "unlock_status": lifecycle.get("unlock_status"),
        "analysis_lifecycle": lifecycle,
        "categories": (project.project_metadata or {}).get("category_summary", {}),
        "next_action": (project.project_metadata or {}).get("next_action") or project_stage_action(workflow_stage),
    }


def serialize_detail(project: Project) -> Dict[str, Any]:
    payload = serialize_summary(project)
    lifecycle = (project.project_metadata or {}).get("analysis_lifecycle", {}) or {}
    payload.update({
        "updated_at": project.updated_at,
        "average_cost": project.average_cost,
        "cost_range_low": project.cost_range_low,
        "cost_range_high": project.cost_range_high,
        "decision_summary": project.decision_summary,
        "analyzer_report": project.analyzer_report or {},
        "strategy": project.strategy or {},
        "procurement_plan": project.procurement_plan or {},
        "metadata": project.project_metadata or {},
        "current_analysis_id": project.current_analysis_id,
        "current_strategy_run_id": project.current_strategy_run_id,
        "current_vendor_match_id": project.current_vendor_match_id,
        "current_rfq_id": project.current_rfq_id,
        "current_quote_id": project.current_quote_id,
        "current_po_id": project.current_po_id,
        "current_shipment_id": project.current_shipment_id,
        "current_invoice_id": project.current_invoice_id,
        "latest_report_version": project.latest_report_version,
        "latest_strategy_version": project.latest_strategy_version,
        "analysis_lifecycle": lifecycle,
    })
    return payload


def build_guest_preview(
    project: Project,
    session_token: Optional[str] = None,
    analysis_status: str = "guest_preview",
    report_visibility_level: str = "preview",
    unlock_status: str = "locked",
) -> Dict[str, Any]:
    report = project.analyzer_report or {}
    section1 = report.get("section_1_executive_summary", {}) or {}
    section2 = report.get("section_2_component_breakdown", []) or []
    section3 = report.get("section_3_sourcing_strategy", {}) or {}
    section5 = report.get("section_5_recommendation", {}) or {}
    lifecycle = (project.project_metadata or {}).get("analysis_lifecycle", {}) or {}

    visible_parts = [
        {
            "part_name": part.get("part_name") or part.get("description") or "Part",
            "category": part.get("category", "unknown"),
            "quantity": _as_int(part.get("quantity"), 1),
            "best_region": (part.get("selected_vendor") or {}).get("region") or part.get("best_region") or project.recommended_location or "Local",
            "process": (part.get("selected_vendor") or {}).get("process_chain", [part.get("process", "")])[0] if isinstance((part.get("selected_vendor") or {}).get("process_chain", []), list) and (part.get("selected_vendor") or {}).get("process_chain") else part.get("process", ""),
            "best_cost": _as_float((part.get("selected_vendor") or {}).get("simulated_tlc") or part.get("best_cost")),
        }
        for part in section2[:3]
    ]

    cost_range = section1.get("cost_breakdown", {}).get("range") or [project.cost_range_low or 0, project.cost_range_high or 0]
    lead = section1.get("lead_time", {}) or {}
    decision_summary = section1.get("decision_summary") or project.decision_summary or report.get("decision_summary", "")
    categories = section1.get("categories") or (project.project_metadata or {}).get("category_summary") or {}
    workspace_route = lifecycle.get("workspace_route") or f"/project/{project.id}"

    return {
        "is_preview": True,
        "guest_bom_id": lifecycle.get("guest_bom_id") or str(project.bom_id),
        "project_id": project.id,
        "session_token": session_token or lifecycle.get("session_token") or "",
        "analysis_status": lifecycle.get("analysis_status") or analysis_status,
        "report_visibility_level": lifecycle.get("report_visibility_level") or report_visibility_level,
        "unlock_status": lifecycle.get("unlock_status") or unlock_status,
        "workspace_route": workspace_route,
        "analysis_lifecycle": {
            "guest_bom_id": lifecycle.get("guest_bom_id") or str(project.bom_id),
            "project_id": project.id,
            "session_token": session_token or lifecycle.get("session_token") or "",
            "analysis_status": lifecycle.get("analysis_status") or analysis_status,
            "report_visibility_level": lifecycle.get("report_visibility_level") or report_visibility_level,
            "unlock_status": lifecycle.get("unlock_status") or unlock_status,
            "workspace_route": workspace_route,
        },
        "currency": section1.get("currency") or project.currency or "USD",
        "cost_range": cost_range,
        "total_cost": section1.get("cost_breakdown", {}).get("total") or project.average_cost or 0,
        "lead_time": {
            "min_days": lead.get("min_days") or project.lead_time or 0,
            "avg_days": lead.get("avg_days") or project.lead_time or 0,
            "max_days": lead.get("max_days") or project.lead_time or 0,
        },
        "savings_percent": section1.get("savings_percent") or project.savings_percent or 0,
        "risk_level": section1.get("risk_level") or (report.get("section_4_financial", {}).get("risk_analysis", {}) or {}).get("overall_risk_level") or "MEDIUM",
        "total_parts": project.total_parts or len(section2),
        "categories": categories,
        "visible_parts": visible_parts,
        "locked_parts_count": max((project.total_parts or len(section2)) - len(visible_parts), 0),
        "basic_processes": section5.get("reasoning") or section1.get("decision_points") or report.get("recommended_reasons", [])[:3],
        "region_distribution": report.get("section_1_executive_summary", {}).get("decision_distribution") or (project.strategy or {}).get("region_distribution", {}),
        "decision_summary": decision_summary,
        "unlock_message": "Sign up to see full BOM breakdown, cost optimization, and procurement plan",
    }


def _component_key(part: Dict[str, Any]) -> str:
    return str(part.get("item_id") or part.get("description") or part.get("part_name") or "")


def _match_part_decision(component: Dict[str, Any], decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    cid = _component_key(component)
    desc = str(component.get("description") or component.get("part_name") or "")
    for item in decisions:
        if str(item.get("item_id") or "") == cid:
            return item
        if desc and (item.get("part_name") == desc or item.get("description") == desc):
            return item
    return decisions[0] if decisions else {}


def _estimate_tlc_breakdown(best_cost: float, best_lead: float, category: str) -> Dict[str, Any]:
    manufacturing = round(best_cost * 0.68, 2)
    logistics = round(best_cost * 0.14, 2)
    tariffs = round(best_cost * 0.06, 2)
    nre = round(best_cost * (0.08 if category in {"custom", "custom_mechanical", "machined", "sheet_metal", "raw_material"} else 0.04), 2)
    inventory = round(best_cost * 0.02, 2)
    risk = round(best_cost * 0.015, 2)
    compliance = round(best_cost * 0.005, 2)
    industrial_tlc = round(manufacturing + logistics + tariffs + nre + inventory + risk + compliance, 2)
    return {
        "c_mfg": manufacturing,
        "quantity": 1,
        "c_log": logistics,
        "c_tariff": tariffs,
        "c_nre": nre,
        "c_inventory": inventory,
        "c_risk": risk,
        "c_compliance": compliance,
        "industrial_tlc": industrial_tlc,
    }


def build_canonical_report(
    raw_analyzer: Dict[str, Any],
    strategy: Dict[str, Any],
    procurement: Dict[str, Any],
    bom: BOM,
    analysis: Optional[AnalysisResult] = None,
) -> Dict[str, Any]:
    analyzer = raw_analyzer or {}
    components = analyzer.get("components", []) or []
    part_decisions = strategy.get("part_level_decisions", []) or []
    procurement_strategy = procurement.get("procurement_strategy", {}) or strategy.get("procurement_strategy", {}) or {}
    cost_summary = procurement.get("cost_summary", {}) or procurement_strategy.get("cost_summary", {}) or {}
    risk = procurement.get("risk_analysis", {}) or procurement_strategy.get("risk_analysis", {}) or {}
    timeline = procurement.get("timeline", {}) or procurement_strategy.get("timeline", {}) or {}
    recommended = strategy.get("recommended_strategy", {}) or {}
    region_distribution = strategy.get("region_distribution", {}) or {}

    cat_counts = analyzer.get("summary", {}).get("categories", {}) or {}
    total_parts = analyzer.get("summary", {}).get("total_items", len(components)) or len(components)
    total_cost = _as_float(cost_summary.get("average"), _as_float(analysis.average_cost if analysis else None, 0.0))
    cost_range = cost_summary.get("range") or [analysis.cost_range_low if analysis else 0, analysis.cost_range_high if analysis else 0]
    if not isinstance(cost_range, list):
        cost_range = [analysis.cost_range_low if analysis else 0, analysis.cost_range_high if analysis else 0]
    if len(cost_range) < 2:
        cost_range = [cost_range[0] if cost_range else 0, cost_range[0] if cost_range else 0]

    section1 = {
        "project_id": bom.id,
        "bom_id": bom.id,
        "name": bom.name or bom.file_name or "Uploaded BOM",
        "total_parts": total_parts,
        "categories": cat_counts,
        "currency": procurement.get("currency") or strategy.get("currency") or "USD",
        "recommended_location": recommended.get("location") or (analysis.recommended_location if analysis else None) or "Local",
        "cost_breakdown": {
            "manufacturing": round(total_cost * 0.68, 2),
            "logistics": round(total_cost * 0.14, 2),
            "tariffs": round(total_cost * 0.06, 2),
            "nre": round(total_cost * 0.08, 2),
            "total": round(total_cost, 2),
            "range": [round(_as_float(cost_range[0]), 2), round(_as_float(cost_range[1]), 2)],
        },
        "lead_time": {
            "min_days": timeline.get("min_days") or 0,
            "avg_days": timeline.get("avg_days") or recommended.get("lead_time") or _as_float(analysis.lead_time if analysis else None),
            "max_days": timeline.get("max_days") or recommended.get("lead_time") or _as_float(analysis.lead_time if analysis else None),
        },
        "decision_distribution": {
            "exploitation_pct": round(max(0.0, 100.0 - min(100.0, _as_float(risk.get("overall_uncertainty"), 0.0) * 100.0)), 1),
            "exploration_pct": round(min(100.0, _as_float(risk.get("overall_uncertainty"), 0.0) * 100.0), 1),
        },
        "risk_level": risk.get("overall_risk_level") or risk.get("risk_level") or "MEDIUM",
        "savings_percent": _as_float(cost_summary.get("savings_percent"), _as_float(analysis.savings_percent if analysis else None)),
        "decision_summary": strategy.get("decision_summary") or (analysis.decision_summary if analysis else "") or "",
        "decision_points": recommended.get("reasons", [])[:3],
        # FIXED: Fields the frontend expects (BOMAnalyzer.jsx Step 5 references these)
        "total_cost": round(total_cost, 2),
        "risk_score": round(min(1.0, _as_float(risk.get("overall_uncertainty"), 0.15)), 3),
        "optimization": {
            "cost_savings_pct": _as_float(cost_summary.get("savings_percent"), _as_float(analysis.savings_percent if analysis else None)),
            "strategy_name": strategy.get("global_optimization", {}).get("best_strategy_name", "per_part_optimized"),
        },
    }

    item_map = {}
    for component in components:
        key = _component_key(component)
        item_map[key] = component
        desc = str(component.get("description") or component.get("part_name") or "")
        if desc:
            item_map.setdefault(desc, component)

    section2 = []
    for idx, decision in enumerate(part_decisions):
        component = item_map.get(str(decision.get("item_id", ""))) or item_map.get(str(decision.get("part_name", "")), {})
        category = str(decision.get("category") or component.get("category") or "standard")
        best_cost = _as_float(decision.get("best_cost"), 0.0)
        lead_days = _as_float(decision.get("lead_days"), _as_float(risk.get("avg_delivery_variance_days"), 0.0))
        selected_vendor = {
            "region": decision.get("best_region") or recommended.get("location") or "Local",
            "transport_mode": "road" if (decision.get("best_region") or "").lower() in {"local", "india", "usa", "mexico", "eu (germany)"} else "air",
            "simulated_tlc": round(best_cost, 2),
            "tlc_breakdown": _estimate_tlc_breakdown(best_cost, lead_days, category),
            "process_chain": [decision.get("process") or component.get("geometry") or "Review", "Procure", "Produce", "Inspect"],
            "machining_time_hrs": round(_as_float(decision.get("unit_price"), 0.0) / 10.0, 2) if category in ("custom", "custom_mechanical", "machined") else round(best_cost / 120.0, 2),
            "labor_hours": round(best_cost / 160.0, 2),
        }
        alternatives = []
        alt_region = decision.get("alternative_region")
        if alt_region:
            alternatives.append({
                "supplier_name": f"Alt Supplier {idx + 1}",
                "region": alt_region,
                "simulated_tlc": round(best_cost * 1.08, 2),
            })
        selected_material = decision.get("material") or component.get("material") or ""
        section2.append({
            "item_id": decision.get("item_id") or component.get("item_id") or str(idx),
            "description": component.get("description") or decision.get("part_name") or decision.get("description") or "Component",
            "part_name": decision.get("part_name") or component.get("description") or component.get("part_name") or "Component",
            "quantity": _as_int(decision.get("quantity"), _as_int(component.get("quantity"), 1)),
            "category": category,
            "material": selected_material,
            "process": decision.get("process") or decision.get("detected_process") or component.get("geometry") or "",
            "price_source": decision.get("price_source") or ("external" if decision.get("unit_price") else "estimated"),
            "unit_price": decision.get("unit_price"),
            "best_region": decision.get("best_region") or selected_vendor["region"],
            "best_cost": best_cost,
            "decision_mode": "exploration" if category in {"custom", "custom_mechanical", "machined", "sheet_metal", "raw_material"} else "exploit",
            "selected_vendor": selected_vendor,
            "explanation": {
                "math": {
                    "ucb": f"best_region={selected_vendor['region']} | cost={best_cost:.2f}",
                    "tlc": f"lead_days={lead_days:.1f} | category={category}",
                },
                "risk": {
                    "supply": round(min(1.0, 0.2 + (_as_float(risk.get("overall_uncertainty"), 0.0))), 3),
                    "logistics": round(min(1.0, 0.15 + (_as_float(risk.get("avg_delivery_variance_days"), 0.0) / 30.0)), 3),
                    "cost_volatility": round(min(1.0, 0.18 + (_as_float(risk.get("overall_uncertainty"), 0.0))), 3),
                    "quality": round(max(0.0, 1.0 - (_as_float(risk.get("overall_uncertainty"), 0.0))), 3),
                },
            },
            "alternatives": alternatives,
            "process_chain": selected_vendor["process_chain"],
            "machining_time_hrs": selected_vendor["machining_time_hrs"],
            "labor_hours": selected_vendor["labor_hours"],
        })

    section3 = {
        "volume_strategy": [
            {
                "type": "high" if item.get("category") == "standard" else ("medium" if item.get("category") in ("custom", "custom_mechanical", "machined") else "low"),
                "item": item.get("description"),
                "qty": item.get("quantity"),
                "region": item.get("best_region"),
                "tlc": item.get("best_cost"),
            }
            for item in section2
        ],
        "process_summary": [
            {
                "item": item.get("description"),
                "material_form": item.get("material") or "general",
                "machining_hrs": item.get("machining_time_hrs"),
                "labor_hrs": item.get("labor_hours"),
                "process_chain": item.get("process_chain") or [],
            }
            for item in section2
            if item.get("category") in {"custom", "custom_mechanical", "machined", "raw_material", "sheet_metal"}
        ],
        "risk_insights": [
            {
                "item": item.get("description"),
                "supplier": (item.get("selected_vendor") or {}).get("region"),
                "variance": round(_as_float((item.get("explanation") or {}).get("risk", {}).get("logistics"), 0.0), 3),
            }
            for item in section2
            if _as_float((item.get("explanation") or {}).get("risk", {}).get("logistics"), 0.0) >= 0.35
        ],
    }

    section4 = {
        "cost_summary": cost_summary,
        "consolidation_report": procurement.get("consolidation_report", {}),
        "local_vs_offshore": procurement.get("local_vs_offshore", {}),
        "global_optimization": procurement.get("global_optimization", {}),
    }

    section5 = {
        "plan": strategy.get("decision_summary") or procurement.get("decision_summary") or project_plan_text(section1, recommended),
        "recommended_strategy": recommended,
        "alternative_strategies": strategy.get("alternative_strategies", []),
        "reasoning": recommended.get("reasons", [])[:3],
    }

    exploration_decisions = [
        {
            "item": item.get("description"),
            "supplier": (item.get("selected_vendor") or {}).get("region"),
            "info_gain": round(0.18 if item.get("category") in {"custom", "custom_mechanical", "machined", "raw_material", "sheet_metal"} else 0.05, 3),
        }
        for item in section2
        if item.get("category") in {"custom", "custom_mechanical", "machined", "raw_material", "sheet_metal"}
    ]
    high_uncertainty = [
        {
            "item": item.get("description"),
            "uncertainty": round(_as_float((item.get("explanation") or {}).get("risk", {}).get("supply"), 0.0), 3),
        }
        for item in section2
        if _as_float((item.get("explanation") or {}).get("risk", {}).get("supply"), 0.0) >= 0.5
    ]
    section6 = {
        "system_confidence": round(max(0.0, 1.0 - _as_float(risk.get("overall_uncertainty"), 0.0)), 3),
        "exploration_rate": round(min(0.5, max(0.05, _as_float(risk.get("overall_uncertainty"), 0.0) * 0.75 + 0.05)), 4),
        "total_iterations": max(1, len(section2)),
        "exploration_decisions": exploration_decisions,
        "high_uncertainty": high_uncertainty,
        "note": "Learning snapshot stored for supplier and pricing memory.",
    }

    raw_meta = analyzer.get("_meta", {})
    # FIXED: Frontend reads meta.items, meta.candidates, meta.total_time_s
    enriched_meta = {
        **raw_meta,
        "items": total_parts,
        "candidates": len(part_decisions),
        "total_time_s": raw_meta.get("total_time_s", 0),
        "version": raw_meta.get("version", "4.1.0"),
        "report_version": 1,
    }

    # ══════════════════════════════════════════════════════════
    # Section 7: Category-wise reports (one per industry category)
    # ══════════════════════════════════════════════════════════
    _ALL_CATS = ["standard", "electrical", "electronics", "fastener",
                 "machined", "custom_mechanical", "sheet_metal",
                 "raw_material", "unknown"]
    _CAT_LABELS = {
        "standard": "Standard / Catalog Parts",
        "electrical": "Electrical Parts",
        "electronics": "Electronics Parts",
        "fastener": "Fasteners",
        "machined": "Machined Parts",
        "custom_mechanical": "Custom Manufacturing",
        "sheet_metal": "Sheet Metal Parts",
        "raw_material": "Raw Materials",
        "unknown": "Unmatched / Needs Review",
    }

    category_reports = {}
    for cat in _ALL_CATS:
        cat_items = [it for it in section2 if (it.get("category") or "unknown") == cat]
        if not cat_items:
            continue
        cat_cost = sum(_as_float(it.get("best_cost"), 0) for it in cat_items)
        cat_qty = sum(_as_int(it.get("quantity"), 1) for it in cat_items)
        rfq_items = [it for it in cat_items if it.get("category") in ("machined", "custom_mechanical", "sheet_metal")]
        category_reports[cat] = {
            "label": _CAT_LABELS.get(cat, cat),
            "count": len(cat_items),
            "total_quantity": cat_qty,
            "total_cost": round(cat_cost, 2),
            "avg_unit_cost": round(cat_cost / max(cat_qty, 1), 4),
            "rfq_required_count": len(rfq_items),
            "items": [
                {
                    "description": it.get("description") or it.get("part_name", ""),
                    "quantity": _as_int(it.get("quantity"), 1),
                    "material": it.get("material", ""),
                    "process": it.get("process", ""),
                    "best_region": it.get("best_region", ""),
                    "best_cost": round(_as_float(it.get("best_cost"), 0), 2),
                    "unit_price": _as_float(it.get("unit_price"), 0),
                    "price_source": it.get("price_source", ""),
                }
                for it in cat_items
            ],
        }

    section7_category_reports = category_reports

    # ══════════════════════════════════════════════════════════
    # Section 8: Substitutes / Alternatives
    # ══════════════════════════════════════════════════════════
    section8_substitutes = {
        "alternatives": [
            {
                "item": it.get("description") or it.get("part_name", ""),
                "current_region": it.get("best_region", ""),
                "current_cost": round(_as_float(it.get("best_cost"), 0), 2),
                "alternative_region": (it.get("alternatives") or [{}])[0].get("region", "") if it.get("alternatives") else "",
                "alternative_cost": round(_as_float((it.get("alternatives") or [{}])[0].get("simulated_tlc"), 0), 2) if it.get("alternatives") else 0,
                "savings": round(
                    _as_float(it.get("best_cost"), 0) - _as_float((it.get("alternatives") or [{}])[0].get("simulated_tlc"), 0), 2
                ) if it.get("alternatives") else 0,
            }
            for it in section2
            if it.get("alternatives")
        ],
    }

    # ══════════════════════════════════════════════════════════
    # Section 9: Risk Summary
    # ══════════════════════════════════════════════════════════
    high_risk_items = [it for it in section2
                       if _as_float((it.get("explanation") or {}).get("risk", {}).get("supply"), 0) >= 0.4]
    section9_risk_summary = {
        "overall_risk_level": risk.get("overall_risk_level") or risk.get("risk_level") or "MEDIUM",
        "overall_uncertainty": round(_as_float(risk.get("overall_uncertainty"), 0.15), 4),
        "avg_delivery_variance_days": round(_as_float(risk.get("avg_delivery_variance_days"), 3), 1),
        "new_vendor_pct": round(_as_float(risk.get("new_vendor_pct"), 0), 1),
        "high_risk_items_count": len(high_risk_items),
        "high_risk_items": [
            {
                "item": it.get("description", ""),
                "region": it.get("best_region", ""),
                "supply_risk": round(_as_float((it.get("explanation") or {}).get("risk", {}).get("supply"), 0), 3),
                "logistics_risk": round(_as_float((it.get("explanation") or {}).get("risk", {}).get("logistics"), 0), 3),
            }
            for it in high_risk_items
        ],
    }

    # ══════════════════════════════════════════════════════════
    # Section 10: Currency Summary
    # ══════════════════════════════════════════════════════════
    report_currency = procurement.get("currency") or strategy.get("currency") or "USD"
    section10_currency_summary = {
        "display_currency": report_currency,
        "source_currency": "USD",
        "fx_rate_note": f"All costs sourced in USD, displayed in {report_currency}" if report_currency != "USD" else "All costs in USD",
        "total_cost_display": round(total_cost, 2),
        "currency_risk": "low" if report_currency == "USD" else "medium",
    }

    # ══════════════════════════════════════════════════════════
    # Section 11: Lead Time Summary
    # ══════════════════════════════════════════════════════════
    lead_times = [_as_float(it.get("selected_vendor", {}).get("tlc_breakdown", {}).get("c_log"), 0) for it in section2]
    part_leads = [_as_float(pd.get("lead_days"), 0) for pd in part_decisions]
    section11_lead_time_summary = {
        "min_days": timeline.get("min_days") or (min(part_leads) if part_leads else 0),
        "avg_days": timeline.get("avg_days") or (round(sum(part_leads) / max(len(part_leads), 1)) if part_leads else 0),
        "max_days": timeline.get("max_days") or (max(part_leads) if part_leads else 0),
        "critical_path_items": [
            {
                "item": pd.get("part_name", ""),
                "lead_days": pd.get("lead_days", 0),
                "region": pd.get("best_region", ""),
            }
            for pd in sorted(part_decisions, key=lambda x: -_as_float(x.get("lead_days"), 0))[:5]
        ],
        "by_category": {
            cat: round(sum(_as_float(it.get("selected_vendor", {}).get("tlc_breakdown", {}).get("c_log"), 14) for it in items) / max(len(items), 1))
            for cat, items in category_reports.items()
        },
    }

    # ══════════════════════════════════════════════════════════
    # Section 12: RFQ Actions
    # ══════════════════════════════════════════════════════════
    rfq_required_items = [it for it in section2 if it.get("category") in ("machined", "custom_mechanical", "sheet_metal")]
    section12_rfq_actions = {
        "total_rfq_items": len(rfq_required_items),
        "drawing_required_count": sum(1 for it in section2 if it.get("category") in ("machined", "custom_mechanical", "sheet_metal")),
        "items": [
            {
                "description": it.get("description", ""),
                "category": it.get("category", ""),
                "quantity": _as_int(it.get("quantity"), 1),
                "material": it.get("material", ""),
                "process": it.get("process", ""),
                "region": it.get("best_region", ""),
                "drawing_required": True,
                "action": "Submit RFQ with drawing for manufacturing quote",
            }
            for it in rfq_required_items
        ],
    }

    # ══════════════════════════════════════════════════════════
    # Section 13: Next Steps
    # ══════════════════════════════════════════════════════════
    steps = []
    std_count = len([it for it in section2 if it.get("category") in ("standard", "electrical", "electronics", "fastener")])
    custom_count = len(rfq_required_items)
    raw_count = len([it for it in section2 if it.get("category") == "raw_material"])
    review_count = len([it for it in section2 if it.get("category") == "unknown"])

    if std_count > 0:
        steps.append({"step": 1, "action": f"Order {std_count} standard/catalog parts from recommended distributors", "priority": "immediate"})
    if custom_count > 0:
        steps.append({"step": 2, "action": f"Submit RFQ with drawings for {custom_count} custom/machined parts", "priority": "high"})
    if raw_count > 0:
        steps.append({"step": 3, "action": f"Source {raw_count} raw material items from regional suppliers", "priority": "medium"})
    if review_count > 0:
        steps.append({"step": 4, "action": f"Engineering review needed for {review_count} unclassified items", "priority": "medium"})
    steps.append({"step": len(steps) + 1, "action": "Consolidate shipments per region cluster to optimize logistics", "priority": "medium"})
    steps.append({"step": len(steps) + 1, "action": "Confirm lead times against production schedule", "priority": "medium"})
    steps.append({"step": len(steps) + 1, "action": "Track milestones: T0 → T1 → T2 → T3 → T4", "priority": "ongoing"})
    steps.append({"step": len(steps) + 1, "action": "Submit execution feedback for supplier learning system", "priority": "post-delivery"})

    section13_next_steps = {
        "steps": steps,
        "summary": f"{len(section2)} parts: {std_count} catalog, {custom_count} RFQ, {raw_count} raw, {review_count} review",
    }

    # ══════════════════════════════════════════════════════════
    # Assemble final report
    # ══════════════════════════════════════════════════════════
    return {
        "section_1_executive_summary": section1,
        "section_2_component_breakdown": section2,
        "section_3_sourcing_strategy": section3,
        "section_4_financial": section4,
        "section_5_recommendation": section5,
        "section_6_learning_snapshot": section6,
        "section_7_category_reports": section7_category_reports,
        "section_8_substitutes": section8_substitutes,
        "section_9_risk_summary": section9_risk_summary,
        "section_10_currency_summary": section10_currency_summary,
        "section_11_lead_time_summary": section11_lead_time_summary,
        "section_12_rfq_actions": section12_rfq_actions,
        "section_13_next_steps": section13_next_steps,
        "decision_summary": section1["decision_summary"],
        "recommended_reasons": recommended.get("reasons", []),
        "_meta": enriched_meta,
        "analyzer": analyzer,
        "strategy": copy.deepcopy(strategy),
        "procurement_plan": copy.deepcopy(procurement),
    }


def project_plan_text(section1: Dict[str, Any], recommended: Dict[str, Any]) -> str:
    location = recommended.get("location") or section1.get("recommended_location") or "Local"
    total = section1.get("cost_breakdown", {}).get("total", 0)
    savings = section1.get("savings_percent", 0)
    return f"Manufacture through {location} with cost-optimized sourcing, projected spend {total:.2f} and savings {savings:.1f}%."

def list_project_events(db: Session, project_id: str, limit: Optional[int] = None) -> List[ProjectEvent]:
    query = (
        db.query(ProjectEvent)
        .filter(ProjectEvent.project_id == project_id)
        .order_by(ProjectEvent.created_at.desc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


@dataclass
class ProjectActionItem:
    project_id: str
    name: Optional[str]
    status: Optional[str]
    workflow_stage: Optional[str]
    rfq_status: Optional[str]
    tracking_stage: Optional[str]
    action: str
    reason: Optional[str]
    updated_at: Optional[datetime]
    cost: Optional[float]
    savings_percent: Optional[float]
    lead_time: Optional[float]

def build_project_metrics(db: Session, user_id: str) -> Dict[str, Any]:
    from collections import Counter
    from datetime import timedelta

    projects = list_projects_for_user(db, user_id)
    now = datetime.utcnow()

    workflow_counts = Counter()
    rfq_counts = Counter()
    pending_approval_items = []
    active_rfq_items = []
    delayed_shipment_items = []
    spend_alert_items = []
    next_actions = []

    total_spend = 0.0
    savings_values = []

    for p in projects:
        stage = normalize_project_stage(p.workflow_stage or p.status, "draft")
        rfq_status = (p.rfq_status or "none").lower()

        workflow_counts[stage] += 1
        rfq_counts[rfq_status] += 1

        total_spend += _as_float(p.average_cost, 0.0)
        if p.savings_percent is not None:
            savings_values.append(_as_float(p.savings_percent, 0.0))

        action = project_stage_action(stage)
        next_actions.append(ProjectActionItem(
            project_id=p.id,
            name=p.name,
            status=stage,
            workflow_stage=stage,
            rfq_status=p.rfq_status,
            tracking_stage=p.tracking_stage,
            action=action,
            reason=(p.decision_summary or "")[:180] if p.decision_summary else action,
            updated_at=p.updated_at,
            cost=_as_float(p.average_cost, None),
            savings_percent=_as_float(p.savings_percent, None),
            lead_time=_as_float(p.lead_time, None),
        ))

        if stage in {"quote_compare", "negotiation", "vendor_selected", "po_issued"} or rfq_status in {"quoted", "partial"}:
            pending_approval_items.append(ProjectActionItem(
                project_id=p.id,
                name=p.name,
                status=stage,
                workflow_stage=stage,
                rfq_status=p.rfq_status,
                tracking_stage=p.tracking_stage,
                action="Review quotes / approve next step",
                reason=p.decision_summary or "Waiting on sourcing approval",
                updated_at=p.updated_at,
                cost=_as_float(p.average_cost, None),
                savings_percent=_as_float(p.savings_percent, None),
                lead_time=_as_float(p.lead_time, None),
            ))

        if rfq_status in {"draft", "sent", "partial", "quoted"} or p.current_rfq_id:
            active_rfq_items.append(ProjectActionItem(
                project_id=p.id,
                name=p.name,
                status=stage,
                workflow_stage=stage,
                rfq_status=p.rfq_status,
                tracking_stage=p.tracking_stage,
                action="Continue RFQ flow",
                reason="RFQ is active or awaiting vendor responses",
                updated_at=p.updated_at,
                cost=_as_float(p.average_cost, None),
                savings_percent=_as_float(p.savings_percent, None),
                lead_time=_as_float(p.lead_time, None),
            ))

        if stage in {"in_production", "qc_inspection", "shipped"} and p.updated_at:
            if (now - p.updated_at.replace(tzinfo=None) if getattr(p.updated_at, "tzinfo", None) else now - p.updated_at).days >= 7:
                delayed_shipment_items.append(ProjectActionItem(
                    project_id=p.id,
                    name=p.name,
                    status=stage,
                    workflow_stage=stage,
                    rfq_status=p.rfq_status,
                    tracking_stage=p.tracking_stage,
                    action="Check shipment delay",
                    reason="Tracking has not advanced recently",
                    updated_at=p.updated_at,
                    cost=_as_float(p.average_cost, None),
                    savings_percent=_as_float(p.savings_percent, None),
                    lead_time=_as_float(p.lead_time, None),
                ))

        budget = _as_float((p.project_metadata or {}).get("budget"), 0.0)
        if budget > 0 and _as_float(p.average_cost, 0.0) > budget:
            spend_alert_items.append(ProjectActionItem(
                project_id=p.id,
                name=p.name,
                status=stage,
                workflow_stage=stage,
                rfq_status=p.rfq_status,
                tracking_stage=p.tracking_stage,
                action="Over budget",
                reason=f"Estimated cost exceeds budget by {(_as_float(p.average_cost, 0.0) - budget):.2f}",
                updated_at=p.updated_at,
                cost=_as_float(p.average_cost, None),
                savings_percent=_as_float(p.savings_percent, None),
                lead_time=_as_float(p.lead_time, None),
            ))
        elif _as_float(p.savings_percent, 0.0) < 0:
            spend_alert_items.append(ProjectActionItem(
                project_id=p.id,
                name=p.name,
                status=stage,
                workflow_stage=stage,
                rfq_status=p.rfq_status,
                tracking_stage=p.tracking_stage,
                action="Margin risk",
                reason="Negative savings percent detected",
                updated_at=p.updated_at,
                cost=_as_float(p.average_cost, None),
                savings_percent=_as_float(p.savings_percent, None),
                lead_time=_as_float(p.lead_time, None),
            ))

    completed_projects = workflow_counts.get("completed", 0)
    open_projects = len(projects) - completed_projects
    average_savings = sum(savings_values) / len(savings_values) if savings_values else 0.0

    return {
        "total_projects": len(projects),
        "open_projects": open_projects,
        "completed_projects": completed_projects,
        "pending_approvals": len(pending_approval_items),
        "active_rfqs": len(active_rfq_items),
        "delayed_shipments": len(delayed_shipment_items),
        "spend_alerts": len(spend_alert_items),
        "total_spend": round(total_spend, 2),
        "average_savings_percent": round(average_savings, 2),
        "workflow_counts": dict(workflow_counts),
        "rfq_counts": dict(rfq_counts),
        "pending_approval_items": [vars(x) for x in pending_approval_items[:5]],
        "active_rfq_items": [vars(x) for x in active_rfq_items[:5]],
        "delayed_shipment_items": [vars(x) for x in delayed_shipment_items[:5]],
        "spend_alert_items": [vars(x) for x in spend_alert_items[:5]],
        "next_actions": [vars(x) for x in next_actions[:8]],
        }