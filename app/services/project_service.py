"""Project service — canonical project projection and reporting adapters."""
from __future__ import annotations

import copy
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.analysis import AnalysisResult
from app.models.bom import BOM
from app.models.project import Project
from app.models.rfq import RFQBatch as RFQ, RFQStatus
from app.models.tracking import ProductionTracking, TrackingStage

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
    mapping = {
        TrackingStage.T0.value: "in_production",
        TrackingStage.T1.value: "in_production",
        TrackingStage.T2.value: "in_production",
        TrackingStage.T3.value: "qc_inspection",
        TrackingStage.T4.value: "shipped",
    }
    return mapping.get(stage, current)


def _project_status_from_rfq(status: Optional[str], current: Optional[str] = None) -> Optional[str]:
    if not status:
        return current
    mapping = {
        RFQStatus.created.value: "quoting",
        RFQStatus.sent.value: "quoting",
        RFQStatus.quoted.value: "quoted",
        RFQStatus.approved.value: "approved",
        RFQStatus.rejected.value: "analyzed",
        RFQStatus.in_production.value: "in_production",
        RFQStatus.completed.value: "completed",
    }
    return mapping.get(status, current)


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
    if not project:
        project = Project(bom_id=bom.id)
        db.add(project)

    cost_summary = procurement.get("cost_summary", {}) or strategy.get("procurement_strategy", {}).get("cost_summary", {}) or {}
    rec = strategy.get("recommended_strategy", {}) or {}
    timeline = procurement.get("timeline", {}) or strategy.get("procurement_strategy", {}).get("timeline", {}) or {}
    risk = procurement.get("risk_analysis", {}) or strategy.get("procurement_strategy", {}).get("risk_analysis", {}) or {}

    project.user_id = bom.user_id or analysis.user_id
    project.name = bom.name or bom.file_name or "Uploaded BOM"
    project.file_name = bom.file_name
    project.status = "analyzed"
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
    project.project_metadata = {
        "category_summary": (analyzer_output or {}).get("summary", {}).get("categories", {}),
        "analysis_id": analysis.id,
        "risk_level": risk.get("overall_risk_level") or risk.get("risk_level"),
    }
    db.flush()
    return project


def update_project_status_from_rfq(db: Session, rfq: RFQ) -> Optional[Project]:
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None
    project.rfq_status = rfq.status
    new_status = _project_status_from_rfq(rfq.status, project.status)
    if new_status:
        project.status = new_status
    db.flush()
    return project


def update_project_status_from_tracking(db: Session, rfq_id: str, stage: Optional[str] = None) -> Optional[Project]:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None
    project.tracking_stage = stage or project.tracking_stage
    new_status = _project_status_from_tracking(stage, project.status)
    if new_status:
        project.status = new_status
    db.flush()
    return project


def sync_project_completion(db: Session, rfq: RFQ) -> Optional[Project]:
    if not rfq or not rfq.bom_id:
        return None
    project = get_project_by_bom_id(db, rfq.bom_id)
    if not project:
        return None
    project.status = "completed"
    project.rfq_status = rfq.status
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
    return {
        "project_id": project.id,
        "name": project.name,
        "status": project.status,
        "total_parts": project.total_parts or 0,
        "created_at": project.created_at,
        "cost": project.average_cost,
        "savings_percent": project.savings_percent,
        "lead_time": project.lead_time,
        "file_name": project.file_name,
        "recommended_location": project.recommended_location,
    }


def serialize_detail(project: Project) -> Dict[str, Any]:
    payload = serialize_summary(project)
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
    })
    return payload


def build_guest_preview(project: Project) -> Dict[str, Any]:
    report = project.analyzer_report or {}
    section1 = report.get("section_1_executive_summary", {}) or {}
    section2 = report.get("section_2_component_breakdown", []) or []
    section3 = report.get("section_3_sourcing_strategy", {}) or {}
    section5 = report.get("section_5_recommendation", {}) or {}

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

    return {
        "is_preview": True,
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
    nre = round(best_cost * (0.08 if category in {"custom", "raw_material"} else 0.04), 2)
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
            "machining_time_hrs": round(_as_float(decision.get("unit_price"), 0.0) / 10.0, 2) if category == "custom" else round(best_cost / 120.0, 2),
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
            "decision_mode": "exploration" if category in {"custom", "raw_material"} else "exploit",
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
                "type": "high" if item.get("category") == "standard" else ("medium" if item.get("category") == "custom" else "low"),
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
            if item.get("category") in {"custom", "raw_material"}
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
            "info_gain": round(0.18 if item.get("category") in {"custom", "raw_material"} else 0.05, 3),
        }
        for item in section2
        if item.get("category") in {"custom", "raw_material"}
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

    return {
        "section_1_executive_summary": section1,
        "section_2_component_breakdown": section2,
        "section_3_sourcing_strategy": section3,
        "section_4_financial": section4,
        "section_5_recommendation": section5,
        "section_6_learning_snapshot": section6,
        "decision_summary": section1["decision_summary"],
        "recommended_reasons": recommended.get("reasons", []),
        "_meta": analyzer.get("_meta", {}),
        "analyzer": analyzer,
        "strategy": copy.deepcopy(strategy),
        "procurement_plan": copy.deepcopy(procurement),
    }


def project_plan_text(section1: Dict[str, Any], recommended: Dict[str, Any]) -> str:
    location = recommended.get("location") or section1.get("recommended_location") or "Local"
    total = section1.get("cost_breakdown", {}).get("total", 0)
    savings = section1.get("savings_percent", 0)
    return f"Manufacture through {location} with cost-optimized sourcing, projected spend {total:.2f} and savings {savings:.1f}%."
