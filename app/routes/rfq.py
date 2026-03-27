"""RFQ routes — updated for sourcing.rfq_batches."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQBatch, RFQItem
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
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
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
    if rfq.status not in ("quoted", "draft"):
        raise HTTPException(status_code=400, detail=f"Cannot approve RFQ in '{rfq.status}' status")
    rfq = rfq_service.update_rfq_status(db, rfq_id, "approved")
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/reject", response_model=RFQResponse)
def reject_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.update_rfq_status(db, rfq_id, "rejected")
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    project_service.update_project_status_from_rfq(db, rfq)
    db.commit()
    return _rfq_to_response(rfq, db)


def _rfq_to_response(rfq: RFQBatch, db: Session) -> RFQResponse:
    items = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq.id).all()
    return RFQResponse(
        id=rfq.id, bom_id=rfq.bom_id, status=rfq.status,
        total_estimated_cost=rfq.total_estimated_cost,
        total_final_cost=rfq.total_final_cost,
        currency=rfq.target_currency, notes=rfq.notes,
        items=[
            RFQItemSchema(
                part_name=i.part_key, quantity=int(i.requested_quantity or 1),
                material=i.requested_material, process=i.requested_process,
                quoted_price=i.quoted_price, lead_time=i.lead_time,
            ) for i in items
        ],
    )
