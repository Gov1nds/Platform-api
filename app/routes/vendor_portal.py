from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import VendorUser
from app.models.rfq import RFQBatch, RFQVendorInvitation, InvitationStatusEvent, RFQQuoteHeader, RFQQuoteLine, PurchaseOrder
from app.models.logistics import Shipment
from app.schemas import QuoteSubmitRequest, QuoteResponse
from app.utils.dependencies import require_vendor_user
from app.services.event_service import track

router = APIRouter(prefix="/vendor-portal", tags=["Vendor Portal"])

def _transition_inv(db, inv, new_status):
    old = inv.status; inv.status = new_status
    db.add(InvitationStatusEvent(invitation_id=inv.id, old_status=old, new_status=new_status))

@router.get("/dashboard")
def vendor_dashboard(vu: VendorUser = Depends(require_vendor_user), db: Session = Depends(get_db)):
    invs = db.query(RFQVendorInvitation).filter(RFQVendorInvitation.vendor_id == vu.vendor_id).order_by(RFQVendorInvitation.created_at.desc()).limit(100).all()
    open_rfqs = [i for i in invs if i.status in ("invited","opened")]
    quoted = db.query(RFQQuoteHeader).filter(RFQQuoteHeader.vendor_id == vu.vendor_id).count()
    orders = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vu.vendor_id).count()
    shipments = db.query(Shipment).join(PurchaseOrder).filter(PurchaseOrder.vendor_id == vu.vendor_id).count()
    return {"vendor_id":vu.vendor_id,"open_rfqs":len(open_rfqs),"total_invitations":len(invs),
        "quotes_submitted":quoted,"active_orders":orders,"active_shipments":shipments,
        "recent_rfqs":[{"id":i.id,"rfq_batch_id":i.rfq_batch_id,"status":i.status,"invited_at":str(i.invited_at)} for i in open_rfqs[:10]]}

@router.get("/rfqs")
def vendor_rfqs(vu: VendorUser = Depends(require_vendor_user), db: Session = Depends(get_db)):
    invs = db.query(RFQVendorInvitation).filter(RFQVendorInvitation.vendor_id == vu.vendor_id).order_by(RFQVendorInvitation.created_at.desc()).all()
    result = []
    for inv in invs:
        rfq = db.query(RFQBatch).filter(RFQBatch.id == inv.rfq_batch_id).first()
        if rfq:
            result.append({"invitation_id":inv.id,"rfq_id":rfq.id,"status":inv.status,"rfq_status":rfq.status,
                "deadline":str(rfq.deadline) if rfq.deadline else None,"item_count":len(rfq.items),"invited_at":str(inv.invited_at)})
    return result

@router.get("/rfqs/{rfq_id}")
def vendor_rfq_detail(rfq_id:str, vu:VendorUser=Depends(require_vendor_user), db:Session=Depends(get_db)):
    inv = db.query(RFQVendorInvitation).filter(RFQVendorInvitation.rfq_batch_id==rfq_id, RFQVendorInvitation.vendor_id==vu.vendor_id).first()
    if not inv: raise HTTPException(403, "Not invited")
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq: raise HTTPException(404)
    if inv.status == "invited":
        _transition_inv(db, inv, "opened"); inv.opened_at = datetime.now(timezone.utc); db.commit()
    # Get existing quotes for revision history
    existing = db.query(RFQQuoteHeader).filter(RFQQuoteHeader.rfq_batch_id==rfq_id, RFQQuoteHeader.vendor_id==vu.vendor_id).order_by(RFQQuoteHeader.quote_version.desc()).all()
    return {"rfq_id":rfq.id,"status":rfq.status,"deadline":str(rfq.deadline) if rfq.deadline else None,"notes":rfq.notes,
        "items":[{"id":i.id,"part_key":i.part_key,"quantity":float(i.requested_quantity),"material":i.requested_material,
            "process":i.requested_process,"drawing_required":i.drawing_required} for i in rfq.items],
        "quote_history":[{"id":q.id,"version":q.quote_version,"status":q.quote_status,"total":float(q.total) if q.total else None,
            "created_at":str(q.created_at)} for q in existing]}

@router.post("/rfqs/{rfq_id}/quote", response_model=QuoteResponse)
def vendor_submit_quote(rfq_id:str, body:QuoteSubmitRequest, vu:VendorUser=Depends(require_vendor_user), db:Session=Depends(get_db)):
    inv = db.query(RFQVendorInvitation).filter(RFQVendorInvitation.rfq_batch_id==rfq_id, RFQVendorInvitation.vendor_id==vu.vendor_id).first()
    if not inv: raise HTTPException(403, "Not invited")
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq: raise HTTPException(404)
    existing_count = db.query(RFQQuoteHeader).filter(RFQQuoteHeader.rfq_batch_id==rfq.id, RFQQuoteHeader.vendor_id==vu.vendor_id).count()
    header = RFQQuoteHeader(rfq_batch_id=rfq.id, vendor_id=vu.vendor_id, quote_number=body.quote_number,
        quote_status="submitted", quote_currency=body.currency, incoterms=body.incoterms,
        valid_until=body.valid_until, quote_version=existing_count+1, is_revision=existing_count>0, notes=body.notes)
    db.add(header); db.flush()
    total = 0
    for ld in body.lines:
        db.add(RFQQuoteLine(quote_header_id=header.id, rfq_item_id=ld.get("rfq_item_id",""),
            part_name=ld.get("part_name",""), quantity=ld.get("quantity",1), unit_price=ld.get("unit_price",0),
            line_currency=body.currency, lead_time_days=ld.get("lead_time_days"), moq=ld.get("moq"), notes=ld.get("notes","")))
        total += float(ld.get("unit_price",0))*float(ld.get("quantity",1))
    header.total = total
    new_inv_status = "fully_quoted" if len(body.lines) >= len(rfq.items) else "partially_quoted"
    _transition_inv(db, inv, new_inv_status); inv.responded_at = datetime.now(timezone.utc)
    track(db,"quote_received",resource_type="rfq",resource_id=rfq.id,payload={"vendor_id":vu.vendor_id,"version":header.quote_version})
    db.commit()
    return QuoteResponse(id=header.id, rfq_batch_id=rfq.id, vendor_id=vu.vendor_id, quote_status="submitted",
        quote_version=header.quote_version, total=float(total),
        lines=[{"part_name":l.part_name,"unit_price":float(l.unit_price) if l.unit_price else None} for l in header.lines],
        created_at=header.created_at)

@router.get("/orders")
def vendor_orders(vu:VendorUser=Depends(require_vendor_user), db:Session=Depends(get_db)):
    orders = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vu.vendor_id).order_by(PurchaseOrder.created_at.desc()).all()
    return [{"id":o.id,"po_number":o.po_number,"status":o.status,"total":float(o.total) if o.total else None,"created_at":str(o.created_at)} for o in orders]

@router.get("/performance")
def vendor_performance(vu:VendorUser=Depends(require_vendor_user), db:Session=Depends(get_db)):
    total_quotes = db.query(RFQQuoteHeader).filter(RFQQuoteHeader.vendor_id == vu.vendor_id).count()
    total_orders = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vu.vendor_id).count()
    completed = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vu.vendor_id, PurchaseOrder.status=="completed").count()
    return {"total_quotes":total_quotes,"total_orders":total_orders,"completed_orders":completed,
        "completion_rate":round(completed/max(total_orders,1)*100,1)}
