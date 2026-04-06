from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import PurchaseOrder, POLineItem
from app.models.logistics import Shipment, ShipmentMilestone
from app.models.project import Project
from app.schemas import POCreateRequest, POResponse, ShipmentCreateRequest, MilestoneCreateRequest, ShipmentResponse
from app.utils.dependencies import require_user, require_project_owner
from app.services.event_service import track
from app.services.workflow.state_machine import transition_project, enforce_po_stage

router = APIRouter(prefix="/orders", tags=["Orders"])

@router.post("/po", response_model=POResponse)
def create_po(body: POCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = require_project_owner(body.project_id, db, user)
    enforce_po_stage(project)
    po = PurchaseOrder(project_id=body.project_id, rfq_batch_id=body.rfq_batch_id, vendor_id=body.vendor_id,
        po_number=f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        status="issued", shipping_terms=body.shipping_terms, payment_terms=body.payment_terms,
        issued_at=datetime.now(timezone.utc))
    db.add(po); db.flush()
    total = 0
    for item in body.line_items:
        tp = float(item.get("quantity",1))*float(item.get("unit_price",0))
        db.add(POLineItem(po_id=po.id, bom_part_id=item.get("bom_part_id"),
            description=item.get("description",""), quantity=item.get("quantity",1),
            unit_price=item.get("unit_price",0), total_price=tp))
        total += tp
    po.total = total; project.current_po_id = po.id
    transition_project(db, project, "po_issued", actor_user_id=user.id)
    track(db,"po_created",actor_id=user.id,resource_type="po",resource_id=po.id)
    db.commit()
    return POResponse.model_validate(po)

@router.get("/po/{po_id}", response_model=POResponse)
def get_po(po_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po: raise HTTPException(404)
    project = db.query(Project).filter(Project.id == po.project_id).first()
    if project and project.user_id != user.id and user.role != "admin": raise HTTPException(403)
    return POResponse.model_validate(po)

@router.get("/po")
def list_pos(project_id:str|None=None, user:User=Depends(require_user), db:Session=Depends(get_db)):
    q = db.query(PurchaseOrder).join(Project).filter(Project.user_id == user.id)
    if project_id: q = q.filter(PurchaseOrder.project_id == project_id)
    return [POResponse.model_validate(p) for p in q.order_by(PurchaseOrder.created_at.desc()).all()]

MILESTONE_STATUS_MAP = {"picked_up":"in_transit","departed":"in_transit","customs_hold":"customs",
    "customs_cleared":"in_transit","out_for_delivery":"out_for_delivery","delivered":"delivered",
    "booking_confirmed":"booked","pickup_scheduled":"pickup_scheduled"}

@router.post("/shipment", response_model=ShipmentResponse)
def create_shipment(body: ShipmentCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == body.po_id).first()
    if not po: raise HTTPException(404)
    project = db.query(Project).filter(Project.id == po.project_id).first()
    if project and project.user_id != user.id and user.role != "admin": raise HTTPException(403)
    shipment = Shipment(po_id=body.po_id, project_id=body.project_id or po.project_id,
        carrier=body.carrier, tracking_number=body.tracking_number, origin=body.origin,
        destination=body.destination, eta=body.eta, status="created")
    db.add(shipment); db.flush()
    db.add(ShipmentMilestone(shipment_id=shipment.id, milestone_type="shipment_created", source="platform"))
    track(db,"shipment_started",actor_id=user.id,resource_type="shipment",resource_id=shipment.id)
    db.commit(); db.refresh(shipment)
    return _ship_resp(shipment)

@router.post("/shipment/milestone")
def add_milestone(body: MilestoneCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    shipment = db.query(Shipment).filter(Shipment.id == body.shipment_id).first()
    if not shipment: raise HTTPException(404)
    # Ownership via PO→Project chain
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.po_id).first()
    if po:
        project = db.query(Project).filter(Project.id == po.project_id).first()
        if project and project.user_id != user.id and user.role != "admin": raise HTTPException(403)
    db.add(ShipmentMilestone(shipment_id=body.shipment_id, milestone_type=body.milestone_type,
        location=body.location, notes=body.notes, is_delay=body.is_delay, source="platform"))
    if body.milestone_type in MILESTONE_STATUS_MAP: shipment.status = MILESTONE_STATUS_MAP[body.milestone_type]
    if body.milestone_type == "delivered":
        shipment.actual_delivery = datetime.now(timezone.utc)
        track(db,"delivered",resource_type="shipment",resource_id=shipment.id)
    db.commit()
    return {"status":"ok"}

@router.get("/shipment/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    s = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not s: raise HTTPException(404)
    # Ownership check via PO→Project chain
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == s.po_id).first()
    if po:
        project = db.query(Project).filter(Project.id == po.project_id).first()
        if project and project.user_id != user.id and user.role != "admin":
            raise HTTPException(403, "Access denied")
    return _ship_resp(s)

def _ship_resp(s):
    return ShipmentResponse(id=s.id, po_id=s.po_id, carrier=s.carrier, tracking_number=s.tracking_number,
        status=s.status, eta=s.eta,
        milestones=[{"type":m.milestone_type,"location":m.location,"notes":m.notes,
            "is_delay":m.is_delay,"occurred_at":str(m.occurred_at),"source":m.source} for m in s.milestones],
        created_at=s.created_at)
