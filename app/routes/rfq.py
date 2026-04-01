"""RFQ routes — normalized quote lifecycle, comparison and selection."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQBatch
from typing import Optional
from app.schemas.rfq import (
    RFQCreateRequest,
    RFQResponse,
    RFQItemSchema,
    RFQQuoteRequest,
    RFQSendRequest,
    RFQSelectRequest,
    RFQRejectVendorRequest,
)
from app.utils.dependencies import require_user
from app.services import rfq_service, email_service
import logging

logger = logging.getLogger("routes.rfq")

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

    db.commit()

    # Send email notification
    try:
        from app.models.rfq import RFQItem
        custom_count = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq.id).count()
        project_name = "BOM Project"
        project_id = rfq.project_id or body.bom_id
        email_service.notify_rfq_submitted(
            user_email=user.email,
            user_name=user.full_name or "",
            project_name=project_name,
            project_id=str(project_id),
            custom_parts_count=custom_count,
        )
    except Exception as e:
        logger.warning(f"RFQ email notification failed: {e}")

    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/send", response_model=RFQResponse)
def send_rfq(
    rfq_id: str,
    body: RFQSendRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        rfq = rfq_service.send_rfq(
            db,
            rfq_id,
            vendor_ids=body.vendor_ids,
            vendor_response_deadline_days=body.vendor_response_deadline_days,
            notes=body.notes,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _rfq_to_response(rfq, db)


@router.get("/{rfq_id}", response_model=RFQResponse)
def get_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return _rfq_to_response(rfq, db)


@router.get("/{rfq_id}/quotes", response_model=RFQResponse)
def get_rfq_quotes(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return _rfq_to_response(rfq, db)


@router.get("/{rfq_id}/compare", response_model=RFQResponse)
def get_rfq_compare(
    rfq_id: str,
    sort_by: str = Query("total_cost"),
    min_vendor_score: float = Query(None),
    max_cost: float = Query(None),
    max_lead_time: float = Query(None),
    max_moq: float = Query(None),
    max_risk: float = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    filters = {
        "min_vendor_score": min_vendor_score,
        "max_cost": max_cost,
        "max_lead_time": max_lead_time,
        "max_moq": max_moq,
        "max_risk": max_risk,
    }
    # rebuild and persist a new snapshot for the compare view
    rfq_service.build_rfq_comparison(db, rfq.id, sort_by=sort_by, filters=filters, persist=True)
    db.commit()
    return _rfq_to_response(rfq, db, comparison_sort=sort_by, comparison_filters=filters)


@router.post("/{rfq_id}/quote", response_model=RFQResponse)
def add_quote(
    rfq_id: str,
    body: RFQQuoteRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")

    quote_meta = {
        "quote_number": body.quote_number,
        "quote_status": body.quote_status,
        "response_status": body.response_status,
        "quote_currency": body.quote_currency,
        "subtotal": body.subtotal,
        "freight": body.freight,
        "taxes": body.taxes,
        "total": body.total,
        "vendor_response_deadline": body.vendor_response_deadline,
        "sent_at": body.sent_at,
        "received_at": body.received_at,
        "expires_at": body.expires_at,
    }

    rfq = rfq_service.add_quote_to_rfq(
        db,
        rfq_id,
        item_quotes=[item.model_dump() for item in body.item_quotes],
        vendor_id=body.vendor_id,
        quote_meta=quote_meta,
    )
    db.commit()

    # Notify user quote is ready
    try:
        if rfq.requested_by_user_id:
            owner = db.query(User).filter(User.id == rfq.requested_by_user_id).first()
            if owner:
                email_service.notify_quote_ready(
                    user_email=owner.email,
                    user_name=owner.full_name or "",
                    project_name="BOM Project",
                    project_id=str(rfq.project_id or rfq.bom_id),
                    total_cost=rfq.total_final_cost,
                    currency=rfq.target_currency or "USD",
                )
    except Exception as e:
        logger.warning(f"Quote ready email failed: {e}")

    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/select", response_model=RFQResponse)
def select_vendor(
    rfq_id: str,
    body: RFQSelectRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        result = rfq_service.select_vendor_for_rfq(
            db,
            rfq_id,
            vendor_id=body.vendor_id,
            quote_id=body.quote_id,
            reason=body.reason,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _rfq_to_response(result["rfq"], db)


@router.post("/{rfq_id}/reject-vendor", response_model=RFQResponse)
def reject_vendor(
    rfq_id: str,
    body: RFQRejectVendorRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        result = rfq_service.reject_vendor_for_rfq(
            db,
            rfq_id,
            vendor_id=body.vendor_id,
            quote_id=body.quote_id,
            reason=body.reason,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _rfq_to_response(result["rfq"], db)


@router.post("/{rfq_id}/approve", response_model=RFQResponse)
def approve_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.get_rfq(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.status not in ("quoted", "draft", "sent", "partial"):
        raise HTTPException(status_code=400, detail=f"Cannot approve RFQ in '{rfq.status}' status")
    rfq = rfq_service.update_rfq_status(db, rfq_id, "approved")
    db.commit()
    return _rfq_to_response(rfq, db)


@router.post("/{rfq_id}/reject", response_model=RFQResponse)
def reject_rfq(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rfq = rfq_service.update_rfq_status(db, rfq_id, "rejected")
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    db.commit()
    return _rfq_to_response(rfq, db)


def _rfq_to_response(
    rfq: RFQBatch,
    db: Session,
    comparison_sort: str = "total_cost",
    comparison_filters: Optional[dict] = None,
) -> RFQResponse:
    items = db.query(rfq_service.RFQItem).filter(rfq_service.RFQItem.rfq_batch_id == rfq.id).all()
    quotes_payload = rfq_service.get_rfq_quotes(db, rfq.id)
    comparison_payload = rfq_service.build_rfq_comparison(
        db,
        rfq.id,
        sort_by=comparison_sort,
        filters=comparison_filters or {},
        persist=False,
    )

    quote_headers = quotes_payload.get("quote_history", [])
    comparison = comparison_payload

    return RFQResponse(
        id=rfq.id,
        bom_id=rfq.bom_id,
        project_id=rfq.project_id,
        status=rfq.status,
        total_estimated_cost=rfq.total_estimated_cost,
        total_final_cost=rfq.total_final_cost,
        currency=rfq.target_currency,
        notes=rfq.notes,
        vendor_response_deadline=rfq.vendor_response_deadline,
        sent_at=rfq.sent_at,
        received_at=rfq.received_at,
        expires_at=rfq.expires_at,
        quote_status=rfq.quote_status,
        response_status=rfq.response_status,
        items=[
            {
                "id": i.id,
                "bom_part_id": i.bom_part_id,
                "part_name": i.part_key,
                "quantity": int(i.requested_quantity or 1),
                "material": i.requested_material,
                "process": i.requested_process,
                "quoted_price": i.quoted_price,
                "lead_time": i.lead_time,
                "drawing_required": i.drawing_required,
                "status": i.status,
                "canonical_part_key": i.canonical_part_key,
                "spec_summary": i.spec_summary or {},
            }
            for i in items
        ],
        quotes=quote_headers,
        comparison=comparison,
    )