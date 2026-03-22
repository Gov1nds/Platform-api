"""RFQ routes — create from analysis, retrieve, approve."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQ, RFQStatus
from app.schemas.rfq import RFQCreateRequest, RFQResponse, RFQItemSchema
from app.utils.dependencies import require_user
from app.services import rfq_service

router = APIRouter(prefix="/rfq", tags=["rfq"])


@router.post("/create", response_model=RFQResponse, status_code=201)
def create_rfq(
    body: RFQCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Create RFQ from analyzed BOM. Requires authentication."""
    try:
        rfq = rfq_service.create_rfq_from_analysis(db, body.bom_id, user.id, body.notes or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return _rfq_to_response(rfq, db)


@router.get("/{rfq_id}", response_model=RFQResponse)
def get_rfq(rfq_id: str, db: Session = Depends(get_db)):
    """Get RFQ details."""
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/approve", response_model=RFQResponse)
def approve_rfq(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Approve an RFQ — transitions to approved status."""
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.status not in (RFQStatus.quoted.value, RFQStatus.created.value):
        raise HTTPException(status_code=400, detail=f"Cannot approve RFQ in '{rfq.status}' status")

    rfq = rfq_service.update_rfq_status(db, rfq_id, RFQStatus.approved.value)
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/reject", response_model=RFQResponse)
def reject_rfq(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Reject an RFQ."""
    rfq = rfq_service.update_rfq_status(db, rfq_id, RFQStatus.rejected.value)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return _rfq_to_response(rfq, db)


def _rfq_to_response(rfq: RFQ, db: Session) -> RFQResponse:
    from app.models.rfq import RFQItem
    items = db.query(RFQItem).filter(RFQItem.rfq_id == rfq.id).all()
    return RFQResponse(
        id=rfq.id,
        bom_id=rfq.bom_id,
        status=rfq.status,
        total_estimated_cost=rfq.total_estimated_cost,
        total_final_cost=rfq.total_final_cost,
        currency=rfq.currency,
        notes=rfq.notes,
        items=[
            RFQItemSchema(
                part_name=i.part_name, quantity=i.quantity,
                material=i.material, process=i.process,
                quoted_price=i.quoted_price, lead_time=i.lead_time,
            ) for i in items
        ],
    )
