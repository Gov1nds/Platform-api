"""Tracking routes — fulfillment lifecycle endpoints.

Thin route layer delegating to tracking_service.
Covers: PO, shipment, carrier milestones, customs, goods receipt, invoice, payment.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.project import Project
from app.schemas.tracking import (
    FulfillmentContextResponse,
    PurchaseOrderCreateRequest,
    PurchaseOrderConfirmRequest,
    ShipmentCreateRequest,
    ShipmentEventCreateRequest,
    CarrierMilestoneCreateRequest,
    CustomsEventCreateRequest,
    GoodsReceiptCreateRequest,
    InvoiceCreateRequest,
    PaymentStateUpdateRequest,
)
from app.schemas.rfq import TrackingResponse, FeedbackRequest
from app.services import tracking_service, project_service
from app.utils.dependencies import require_user, can_access_project, build_project_access_context
from app.services.workflow_service import begin_command, complete_command, fail_command

logger = logging.getLogger("routes.tracking")
router = APIRouter(prefix="/tracking", tags=["tracking"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_rfq_project(db: Session, rfq_id: str):
    """Look up the RFQ and its related Project for access control."""
    from app.models.rfq import RFQBatch as RFQ
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return None, None
    project = project_service.get_project_by_bom_id(db, str(rfq.bom_id)) if rfq.bom_id else None
    return rfq, project


def _require_tracking_access(db: Session, rfq_id: str, user: User):
    rfq, project = _resolve_rfq_project(db, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if project and can_access_project(user, project, db):
        return rfq, project
    if rfq.requested_by_user_id and str(rfq.requested_by_user_id) == str(user.id):
        return rfq, project
    raise HTTPException(status_code=403, detail="Not authorized")


def _require_po_access(db: Session, po_id: str, user: User):
    from app.models.tracking import PurchaseOrder
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq, project = _resolve_rfq_project(db, str(po.rfq_id))
    if project and can_access_project(user, project, db):
        return po, rfq, project
    if rfq and rfq.requested_by_user_id and str(rfq.requested_by_user_id) == str(user.id):
        return po, rfq, project
    raise HTTPException(status_code=403, detail="Not authorized")


def _require_shipment_access(db: Session, shipment_id: str, user: User):
    from app.models.tracking import Shipment
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    po, rfq, project = _require_po_access(db, str(shipment.purchase_order_id), user)
    return shipment, po, rfq, project


def _require_invoice_access(db: Session, invoice_id: str, user: User):
    from app.models.tracking import Invoice
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    po, rfq, project = _require_po_access(db, str(invoice.purchase_order_id), user)
    return invoice, po, rfq, project


# ── GET fulfillment context ──────────────────────────────────────────────────

@router.get("/rfq/{rfq_id}")
def get_fulfillment_context(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq, project = _require_tracking_access(db, rfq_id, user)
    try:
        result = tracking_service.get_fulfillment_context(db, rfq_id)
        if project:
            result["access"] = build_project_access_context(user, project, db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Legacy stage tracking ────────────────────────────────────────────────────

@router.post("/rfq/{rfq_id}/advance", response_model=TrackingResponse)
def advance_tracking_stage(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_tracking_access(db, rfq_id, user)
    try:
        result = tracking_service.advance_stage(db, rfq_id, updated_by=user.id)
        if not result:
            raise HTTPException(status_code=400, detail="Cannot advance stage")
        db.commit()
        return {
            "rfq_id": rfq_id,
            "stage": result.stage,
            "status_message": result.status_message,
            "progress_percent": result.progress_percent,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ── Purchase Order ───────────────────────────────────────────────────────────

@router.post("/rfq/{rfq_id}/purchase-order")
def create_purchase_order(
    rfq_id: str,
    body: PurchaseOrderCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    rfq, project = _require_tracking_access(db, rfq_id, user)

    command, cached = begin_command(
        db,
        namespace="tracking.create_po",
        idempotency_key=idempotency_key,
        payload={"rfq_id": rfq_id, **body.model_dump(mode="json")},
        request_method="POST",
        request_path=f"/api/v1/tracking/rfq/{rfq_id}/purchase-order",
        user_id=user.id,
        related_id=rfq_id,
    )
    if cached:
        return cached

    try:
        result = tracking_service.create_purchase_order(
            db=db,
            rfq_id=rfq_id,
            user=user,
            vendor_id=body.vendor_id,
            po_number=body.po_number,
            currency=body.currency,
            subtotal=body.subtotal,
            freight=body.freight,
            taxes=body.taxes,
            total_amount=body.total_amount,
            notes=body.notes,
            metadata=body.metadata,
        )
        complete_command(db, command, result)
        db.commit()
        return result
    except ValueError as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        fail_command(db, command, str(e))
        db.rollback()
        raise


@router.post("/purchase-orders/{po_id}/confirm")
def confirm_purchase_order(
    po_id: str,
    body: PurchaseOrderConfirmRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_po_access(db, po_id, user)
    try:
        result = tracking_service.confirm_purchase_order(
            db=db,
            po_id=po_id,
            user=user,
            vendor_confirmation_number=body.vendor_confirmation_number,
            notes=body.notes,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


# ── Shipments ────────────────────────────────────────────────────────────────

@router.post("/purchase-orders/{po_id}/shipments")
def create_shipment(
    po_id: str,
    body: ShipmentCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_po_access(db, po_id, user)
    try:
        result = tracking_service.create_shipment(
            db=db,
            po_id=po_id,
            user=user,
            carrier_name=body.carrier_name,
            carrier_code=body.carrier_code,
            tracking_number=body.tracking_number,
            tracking_number_source=body.tracking_number_source,
            tracking_reference=body.tracking_reference,
            status=body.status,
            eta=body.eta,
            origin=body.origin,
            destination=body.destination,
            delay_reason=body.delay_reason,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/events")
def add_shipment_event(
    shipment_id: str,
    body: ShipmentEventCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_shipment_access(db, shipment_id, user)
    try:
        result = tracking_service.add_shipment_event(
            db=db,
            shipment_id=shipment_id,
            event_type=body.event_type,
            event_status=body.event_status,
            location=body.location,
            message=body.message,
            occurred_at=body.occurred_at,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/milestones")
def add_carrier_milestone(
    shipment_id: str,
    body: CarrierMilestoneCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_shipment_access(db, shipment_id, user)
    try:
        result = tracking_service.add_carrier_milestone(
            db=db,
            shipment_id=shipment_id,
            milestone_code=body.milestone_code,
            milestone_name=body.milestone_name,
            milestone_status=body.milestone_status,
            description=body.description,
            location=body.location,
            estimated_at=body.estimated_at,
            actual_at=body.actual_at,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/customs")
def add_customs_event(
    shipment_id: str,
    body: CustomsEventCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_shipment_access(db, shipment_id, user)
    try:
        result = tracking_service.add_customs_event(
            db=db,
            shipment_id=shipment_id,
            country=body.country,
            status=body.status,
            message=body.message,
            held_reason=body.held_reason,
            released_at=body.released_at,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


# ── Goods Receipt ────────────────────────────────────────────────────────────

@router.post("/purchase-orders/{po_id}/receipts")
def confirm_goods_receipt(
    po_id: str,
    body: GoodsReceiptCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_po_access(db, po_id, user)
    try:
        result = tracking_service.confirm_goods_receipt(
            db=db,
            po_id=po_id,
            user=user,
            receipt_number=body.receipt_number,
            receipt_status=body.receipt_status,
            received_quantity=body.received_quantity,
            confirmed_at=body.confirmed_at,
            notes=body.notes,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


# ── Invoices ─────────────────────────────────────────────────────────────────

@router.post("/purchase-orders/{po_id}/invoices")
def create_invoice(
    po_id: str,
    body: InvoiceCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_po_access(db, po_id, user)
    try:
        result = tracking_service.create_invoice(
            db=db,
            po_id=po_id,
            user=user,
            vendor_id=body.vendor_id,
            invoice_number=body.invoice_number,
            invoice_date=body.invoice_date,
            due_date=body.due_date,
            invoice_status=body.invoice_status,
            currency=body.currency,
            subtotal=body.subtotal,
            taxes=body.taxes,
            total_amount=body.total_amount,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


# ── Payment ──────────────────────────────────────────────────────────────────

@router.post("/invoices/{invoice_id}/payment")
def update_payment_state(
    invoice_id: str,
    body: PaymentStateUpdateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_invoice_access(db, invoice_id, user)
    try:
        result = tracking_service.update_payment_state(
            db=db,
            invoice_id=invoice_id,
            user=user,
            status=body.status,
            paid_at=body.paid_at,
            payment_reference=body.payment_reference,
            notes=body.notes,
            metadata=body.metadata,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise


# ── Feedback (legacy) ────────────────────────────────────────────────────────

@router.post("/rfq/{rfq_id}/feedback")
def submit_feedback(
    rfq_id: str,
    body: FeedbackRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_tracking_access(db, rfq_id, user)
    try:
        tracking_service.submit_feedback(
            db=db,
            rfq_id=rfq_id,
            actual_cost=body.actual_cost,
            actual_lead_time=body.actual_lead_time,
            feedback_notes=body.feedback_notes,
        )
        db.commit()
        return {"status": "recorded", "rfq_id": rfq_id}
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise
