"""Canonical workflow state machine — enforced at API boundary."""
from fastapi import HTTPException
from app.models.project import Project, ProjectEvent

TRANSITIONS = {
    "draft":["analyzing","cancelled"],
    "analyzing":["analyzed","draft"],
    "analyzed":["strategy","vendor_match","rfq_pending","cancelled"],
    "strategy":["vendor_match","rfq_pending","cancelled"],
    "vendor_match":["rfq_pending","cancelled"],
    "rfq_pending":["rfq_sent","cancelled"],
    "rfq_sent":["quote_compare","cancelled"],
    "quote_compare":["negotiation","vendor_selected","cancelled"],
    "negotiation":["vendor_selected","quote_compare","cancelled"],
    "vendor_selected":["po_issued","cancelled"],
    "po_issued":["in_production","cancelled"],
    "in_production":["qc_inspection","shipped","cancelled"],
    "qc_inspection":["shipped","in_production","cancelled"],
    "shipped":["delivered","cancelled"],
    "delivered":["completed"],
    "completed":[],
    "cancelled":[],
}

RFQ_ALLOWED_STAGES = {"analyzed","strategy","vendor_match","rfq_pending"}
PO_ALLOWED_STAGES = {"vendor_selected","negotiation","quote_compare"}

def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, [])

def transition_project(db, project: Project, new_status: str, actor_user_id: str | None = None, payload: dict | None = None) -> Project:
    if not can_transition(project.status, new_status):
        raise HTTPException(400, f"Cannot transition from '{project.status}' to '{new_status}'")
    old = project.status
    project.status = new_status
    db.add(ProjectEvent(project_id=project.id, event_type="status_change",
        old_status=old, new_status=new_status, actor_user_id=actor_user_id, payload=payload or {}))
    return project

def enforce_rfq_stage(project: Project):
    if project.status not in RFQ_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create RFQ from project stage '{project.status}'")

def enforce_po_stage(project: Project):
    if project.status not in PO_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create PO from project stage '{project.status}'")
