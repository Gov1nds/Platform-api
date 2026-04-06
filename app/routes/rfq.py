import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.bom import BOMPart
from app.models.project import Project
from app.models.rfq import RFQBatch, RFQItem, RFQVendorInvitation, InvitationStatusEvent, RFQQuoteHeader, RFQQuoteLine
from app.schemas import RFQCreateRequest, RFQResponse, QuoteSubmitRequest, QuoteResponse
from app.utils.dependencies import require_user, require_project_owner
from app.services.event_service import track
from app.services.workflow.state_machine import enforce_rfq_stage, transition_project

router = APIRouter(prefix="/rfq", tags=["RFQ"])

def _rfq_response(rfq):
    return RFQResponse(id=rfq.id, project_id=rfq.project_id, bom_id=rfq.bom_id, status=rfq.status,
        notes=rfq.notes, deadline=rfq.deadline,
        items=[{"id":i.id,"part_key":i.part_key,"quantity":float(i.requested_quantity)} for i in rfq.items],
        invitations=[{"id":inv.id,"vendor_id":inv.vendor_id,"status":inv.status} for inv in rfq.invitations],
        quotes=[{"id":h.id,"vendor_id":h.vendor_id,"quote_status":h.quote_status,"total":float(h.total) if h.total else None,"quote_version":h.quote_version} for h in rfq.quote_headers],
        created_at=rfq.created_at)

@router.post("/create", response_model=RFQResponse)
def create_rfq(body: RFQCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Ownership + stage enforcement
    project = require_project_owner(body.project_id, db, user)
    enforce_rfq_stage(project)
    parts = db.query(BOMPart).filter(BOMPart.bom_id == body.bom_id).all()
    if not parts: raise HTTPException(400, "No parts found")

    rfq = RFQBatch(bom_id=body.bom_id, project_id=body.project_id, requested_by_user_id=user.id,
        status="draft", notes=body.notes, deadline=body.deadline)
    db.add(rfq); db.flush()
    for part in parts:
        db.add(RFQItem(rfq_batch_id=rfq.id, bom_part_id=part.id,
            part_key=part.canonical_part_key or part.description or "",
            requested_quantity=part.quantity, requested_material=part.material,
            requested_process=part.procurement_class, drawing_required=part.drawing_required))
    for vid in body.vendor_ids:
        inv = RFQVendorInvitation(rfq_batch_id=rfq.id, vendor_id=vid, status="invited", portal_token=str(uuid.uuid4()))
        db.add(inv); db.flush()
        db.add(InvitationStatusEvent(invitation_id=inv.id, new_status="invited"))
    if body.vendor_ids:
        rfq.status = "sent"
        transition_project(db, project, "rfq_sent", actor_user_id=user.id)
    track(db,"rfq_sent",actor_id=user.id,resource_type="rfq",resource_id=rfq.id)
    db.commit(); db.refresh(rfq)
    return _rfq_response(rfq)

@router.get("/{rfq_id}", response_model=RFQResponse)
def get_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq: raise HTTPException(404, "RFQ not found")
    if rfq.requested_by_user_id != user.id and user.role != "admin":
        raise HTTPException(403, "Access denied")
    return _rfq_response(rfq)

@router.get("")
def list_rfqs(project_id:str|None=None, user:User=Depends(require_user), db:Session=Depends(get_db)):
    q = db.query(RFQBatch).filter(RFQBatch.requested_by_user_id == user.id)
    if project_id: q = q.filter(RFQBatch.project_id == project_id)
    return [_rfq_response(r) for r in q.order_by(RFQBatch.created_at.desc()).all()]

@router.get("/{rfq_id}/quotes")
def get_quotes(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq: raise HTTPException(404)
    if rfq.requested_by_user_id != user.id and user.role != "admin": raise HTTPException(403)
    return [QuoteResponse(id=h.id, rfq_batch_id=rfq_id, vendor_id=h.vendor_id, quote_status=h.quote_status,
        quote_version=h.quote_version, total=float(h.total) if h.total else None,
        lines=[{"part_name":l.part_name,"unit_price":float(l.unit_price) if l.unit_price else None,
                "quantity":float(l.quantity),"lead_time_days":float(l.lead_time_days) if l.lead_time_days else None,
                "moq":float(l.moq) if l.moq else None,"notes":l.notes} for l in h.lines],
        created_at=h.created_at) for h in rfq.quote_headers]

@router.post("/quote/submit", response_model=QuoteResponse)
def submit_quote(body: QuoteSubmitRequest, db: Session = Depends(get_db)):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == body.rfq_batch_id).first()
    if not rfq: raise HTTPException(404)
    # Check existing versions
    existing_count = db.query(RFQQuoteHeader).filter(
        RFQQuoteHeader.rfq_batch_id==rfq.id, RFQQuoteHeader.vendor_id==body.vendor_id).count()
    header = RFQQuoteHeader(rfq_batch_id=rfq.id, vendor_id=body.vendor_id, quote_number=body.quote_number,
        quote_status="received", quote_currency=body.currency, incoterms=body.incoterms,
        valid_until=body.valid_until, quote_version=existing_count+1,
        is_revision=existing_count>0, notes=body.notes)
    db.add(header); db.flush()
    total = 0
    for ld in body.lines:
        db.add(RFQQuoteLine(quote_header_id=header.id, rfq_item_id=ld.get("rfq_item_id",""),
            part_name=ld.get("part_name",""), quantity=ld.get("quantity",1),
            unit_price=ld.get("unit_price",0), line_currency=body.currency,
            lead_time_days=ld.get("lead_time_days"), moq=ld.get("moq"), notes=ld.get("notes","")))
        total += float(ld.get("unit_price",0)) * float(ld.get("quantity",1))
    header.total = total; rfq.status = "quoted"
    track(db,"quote_received",resource_type="rfq",resource_id=rfq.id,payload={"vendor_id":body.vendor_id,"version":header.quote_version})
    db.commit()
    return QuoteResponse(id=header.id, rfq_batch_id=rfq.id, vendor_id=body.vendor_id,
        quote_status="received", quote_version=header.quote_version, total=float(total),
        lines=[{"part_name":l.part_name,"unit_price":float(l.unit_price) if l.unit_price else None} for l in header.lines],
        created_at=header.created_at)
