from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.models.user import User
from app.models.project import Project
from app.models.rfq import RFQBatch, PurchaseOrder
from app.models.bom import BOMPart
from app.models.logistics import Shipment
from app.models.events import ReportSnapshot
from app.schemas import ReportRequest, ReportResponse
from app.utils.dependencies import require_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])

@router.get("/dashboard")
def dashboard_analytics(user: User = Depends(require_user), db: Session = Depends(get_db)):
    uid = user.id
    total_p = db.query(Project).filter(Project.user_id==uid).count()
    active_p = db.query(Project).filter(Project.user_id==uid, Project.status.notin_(["completed","cancelled"])).count()
    total_rfqs = db.query(RFQBatch).filter(RFQBatch.requested_by_user_id==uid).count()
    pending_rfqs = db.query(RFQBatch).filter(RFQBatch.requested_by_user_id==uid, RFQBatch.status.in_(["sent","draft"])).count()
    total_pos = db.query(PurchaseOrder).join(Project).filter(Project.user_id==uid).count()
    active_shipments = db.query(Shipment).join(PurchaseOrder).join(Project).filter(Project.user_id==uid, Shipment.status.notin_(["delivered","cancelled"])).count()
    total_spend = 0.0
    pos = db.query(PurchaseOrder).join(Project).filter(Project.user_id==uid, PurchaseOrder.total.isnot(None)).all()
    total_spend = sum(float(po.total) for po in pos)
    cats = db.query(BOMPart.category_code, func.count(BOMPart.id)).join(Project, Project.bom_id==BOMPart.bom_id).filter(Project.user_id==uid).group_by(BOMPart.category_code).all()
    stats = db.query(Project.status, func.count(Project.id)).filter(Project.user_id==uid).group_by(Project.status).all()
    # Recent projects for "continue where you left off"
    recent = db.query(Project).filter(Project.user_id==uid, Project.status.notin_(["completed","cancelled"])).order_by(Project.updated_at.desc()).limit(5).all()
    return {
        "total_projects":total_p,"active_projects":active_p,"total_rfqs":total_rfqs,"pending_rfqs":pending_rfqs,
        "total_pos":total_pos,"active_shipments":active_shipments,"total_spend":round(total_spend,2),
        "category_breakdown":{c or "unknown":cnt for c,cnt in cats},
        "status_breakdown":{s:cnt for s,cnt in stats},
        "continue_where_left_off":[{"id":p.id,"name":p.name,"status":p.status,"updated_at":str(p.updated_at)} for p in recent],
    }

@router.post("/reports", response_model=ReportResponse)
def generate_report(body: ReportRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    uid = user.id
    data = {}; summary = {}
    if body.report_type == "executive":
        tp = db.query(Project).filter(Project.user_id==uid).count()
        comp = db.query(Project).filter(Project.user_id==uid, Project.status=="completed").count()
        spend = sum(float(po.total) for po in db.query(PurchaseOrder).join(Project).filter(Project.user_id==uid, PurchaseOrder.total.isnot(None)).all())
        data = {"total_projects":tp,"completed":comp,"total_spend":round(spend,2)}
        summary = {"completion_rate":round(comp/max(tp,1)*100,1),"avg_spend":round(spend/max(comp,1),2)}
    elif body.report_type == "spend":
        pos = db.query(PurchaseOrder).join(Project).filter(Project.user_id==uid, PurchaseOrder.total.isnot(None)).all()
        spend = sum(float(po.total) for po in pos)
        by_vendor = {}
        for po in pos:
            vid = str(po.vendor_id or "unknown")
            by_vendor[vid] = by_vendor.get(vid,0) + float(po.total)
        data = {"total_spend":round(spend,2),"po_count":len(pos),"by_vendor":by_vendor}
        summary = {"avg_po_value":round(spend/max(len(pos),1),2)}
    elif body.report_type == "category":
        cats = db.query(BOMPart.category_code, func.count(BOMPart.id)).join(Project, Project.bom_id==BOMPart.bom_id).filter(Project.user_id==uid).group_by(BOMPart.category_code).all()
        data = {"categories":{c or "unknown":cnt for c,cnt in cats}}
        summary = {"total_categories":len(cats)}
    elif body.report_type == "supplier":
        from app.models.vendor import VendorMatch
        matches = db.query(VendorMatch).join(Project).filter(Project.user_id==uid).all()
        vendor_scores = {}
        for m in matches:
            vid = str(m.vendor_id)
            if vid not in vendor_scores: vendor_scores[vid] = []
            vendor_scores[vid].append(float(m.score))
        data = {"vendor_avg_scores":{vid:round(sum(s)/len(s),4) for vid,s in vendor_scores.items()}}
        summary = {"vendors_evaluated":len(vendor_scores)}
    elif body.report_type == "risk":
        from app.models.bom import AnalysisResult
        analyses = db.query(AnalysisResult).join(Project, AnalysisResult.project_id==Project.id).filter(Project.user_id==uid).all()
        high_risk = 0; total_items = 0
        for a in analyses:
            comps = (a.report_json or {}).get("components",[])
            for c in comps:
                total_items += 1
                if (c.get("risk_assessment") or {}).get("risk_level") == "high": high_risk += 1
        data = {"total_items":total_items,"high_risk_items":high_risk}
        summary = {"high_risk_pct":round(high_risk/max(total_items,1)*100,1)}
    elif body.report_type == "operational":
        active = db.query(Project).filter(Project.user_id==uid, Project.status.notin_(["completed","cancelled"])).count()
        delayed_shipments = db.query(Shipment).join(PurchaseOrder).join(Project).filter(Project.user_id==uid).join(
            # simplified — check for any delay milestone
        ).count() if False else 0
        data = {"active_projects":active}
        summary = {}
    else:
        data = {"report_type":body.report_type}; summary = {}

    snap = ReportSnapshot(report_type=body.report_type, scope_type=body.scope_type, scope_id=body.scope_id,
        filters_json=body.filters, data_json=data, summary_json=summary)
    db.add(snap); db.commit(); db.refresh(snap)
    return ReportResponse.model_validate(snap)


# ── Individual report endpoints (Blueprint Section 15) ───────────────────────

from datetime import date as _date

@router.get("/reports/spend")
def report_spend(
    date_from: _date | None = None, date_to: _date | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.spend_analysis(db, org_id, date_from, date_to)


@router.get("/reports/savings")
def report_savings(
    date_from: _date | None = None, date_to: _date | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.savings_vs_baseline(db, org_id, date_from, date_to)


@router.get("/reports/supplier-performance")
def report_supplier_performance(
    date_from: _date | None = None, date_to: _date | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.supplier_performance(db, org_id, date_from, date_to)


@router.get("/reports/operational-status")
def report_operational(
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.operational_status(db, org_id)


@router.get("/reports/lead-time")
def report_lead_time(
    date_from: _date | None = None, date_to: _date | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.lead_time_analysis(db, org_id, date_from, date_to)


@router.get("/reports/risk")
def report_risk(
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.risk_dashboard(db, org_id)


@router.get("/reports/quote-intelligence")
def report_quote_intel(
    date_from: _date | None = None, date_to: _date | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.quote_intelligence(db, org_id, date_from, date_to)


@router.get("/reports/category-insights")
def report_category(
    category: str | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from app.services.report_service import report_service
    org_id = getattr(user, "organization_id", None) or ""
    return report_service.category_insights(db, org_id, category)


@router.get("/reports/{report_id}/export")
def export_report(
    report_id: str,
    format: str = Query("pdf", description="pdf or xlsx"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Export a saved report snapshot as PDF."""
    snap = db.query(ReportSnapshot).filter(ReportSnapshot.id == report_id).first()
    if not snap:
        from fastapi import HTTPException
        raise HTTPException(404, "Report not found")
    if format == "pdf":
        from app.services.report_service import report_service
        pdf_bytes = report_service.export_pdf(snap.report_type, snap.data_json or {})
        if not pdf_bytes:
            raise HTTPException(501, "PDF export not available (reportlab not installed)")
        from fastapi.responses import Response
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=report_{report_id[:8]}.pdf"})
    return {"error": "Only PDF export is currently supported"}
