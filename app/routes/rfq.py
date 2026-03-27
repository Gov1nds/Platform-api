"""RFQ routes — FIXED: GET rfq requires auth + ownership check."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQ, RFQStatus
from app.schemas.rfq import RFQCreateRequest, RFQResponse, RFQItemSchema, RFQQuoteRequest
from app.utils.dependencies import require_user
from app.services import rfq_service, project_service

router = APIRouter(prefix="/rfq", tags=["rfq"])


@router.post("/create", response_model=RFQResponse, status_code=201)
def create_rfq(
    body: RFQCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        rfq = rfq_service.create_rfq_from_analysis(db, body.bom_id, user.id, body.notes or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


@router.get("/{rfq_id}", response_model=RFQResponse)
def get_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """FIXED: now requires auth + ownership check."""
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.user_id and rfq.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/quote", response_model=RFQResponse)
def add_quote(
    rfq_id: str, body: RFQQuoteRequest,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    rfq = rfq_service.add_quote_to_rfq(
        db, rfq_id, item_quotes=[item.model_dump() for item in body.item_quotes],
        vendor_id=body.vendor_id,
    )
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/approve", response_model=RFQResponse)
def approve_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.status not in (RFQStatus.quoted.value, RFQStatus.created.value):
        raise HTTPException(status_code=400, detail=f"Cannot approve RFQ in '{rfq.status}' status")
    rfq = rfq_service.update_rfq_status(db, rfq_id, RFQStatus.approved.value)
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/reject", response_model=RFQResponse)
def reject_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.update_rfq_status(db, rfq_id, RFQStatus.rejected.value)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


def _rfq_to_response(rfq: RFQ, db: Session) -> RFQResponse:
    from app.models.rfq import RFQItem
    items = db.query(RFQItem).filter(RFQItem.rfq_id == rfq.id).all()
    return RFQResponse(
        id=rfq.id, bom_id=rfq.bom_id, status=rfq.status,
        total_estimated_cost=rfq.total_estimated_cost,
        total_final_cost=rfq.total_final_cost,
        currency=rfq.currency, notes=rfq.notes,
        items=[
            RFQItemSchema(
                part_name=i.part_name, quantity=i.quantity, material=i.material,
                process=i.process, quoted_price=i.quoted_price, lead_time=i.lead_time,
            ) for i in items
        ],
    )