"""Tracking routes — order center, fulfillment context, shipments, receipts, invoices, payments."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.utils.dependencies import require_user, require_roles
from app.schemas.tracking import (
    FulfillmentContextResponse,
    TrackingEntrySchema,
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
from app.services import tracking_service
from app.services.workflow_service import begin_command, complete_command, fail_command
import logging

logger = logging.getLogger("routes.tracking")

router = APIRouter(prefix="/tracking", tags=["tracking"])


@router.get("/rfq/{rfq_id}", response_model=FulfillmentContextResponse)
def get_tracking(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Returns the full fulfillment context, not just stage history."""
    try:
        return tracking_service.get_fulfillment_context(db, rfq_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/rfq/{rfq_id}/start", response_model=TrackingEntrySchema)
def start_production(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.start",
        idempotency_key=idempotency_key,
        payload={"rfq_id": rfq_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/rfq/{rfq_id}/start",
        user_id=user.id,
        related_id=rfq_id,
    )
    if cached:
        return TrackingEntrySchema.model_validate(cached)

    try:
        entry = tracking_service.create_tracking(db, rfq_id)
        response = {
            "id": entry.id,
            "rfq_id": entry.rfq_id,
            "stage": entry.stage,
            "execution_state": entry.execution_state,
            "status_message": entry.status_message,
            "progress_percent": entry.progress_percent,
            "po_id": entry.po_id,
            "shipment_id": entry.shipment_id,
            "invoice_id": entry.invoice_id,
            "delay_reason": entry.delay_reason,
            "context_json": entry.context_json or {},
            "updated_by": entry.updated_by,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
        complete_command(db, command, response)
        db.commit()
        return response
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/rfq/{rfq_id}/advance", response_model=TrackingEntrySchema)
def advance_stage(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.advance",
        idempotency_key=idempotency_key,
        payload={"rfq_id": rfq_id, "user_id": user.id, "updated_by": user.email},
        request_method="POST",
        request_path=f"/api/v1/tracking/rfq/{rfq_id}/advance",
        user_id=user.id,
        related_id=rfq_id,
    )
    if cached:
        return TrackingEntrySchema.model_validate(cached)

    try:
        entry = tracking_service.advance_stage(db, rfq_id, updated_by=user.email)
        if not entry:
            raise HTTPException(status_code=404, detail="Tracking not found")
        response = {
            "id": entry.id,
            "rfq_id": entry.rfq_id,
            "stage": entry.stage,
            "execution_state": entry.execution_state,
            "status_message": entry.status_message,
            "progress_percent": entry.progress_percent,
            "po_id": entry.po_id,
            "shipment_id": entry.shipment_id,
            "invoice_id": entry.invoice_id,
            "delay_reason": entry.delay_reason,
            "context_json": entry.context_json or {},
            "updated_by": entry.updated_by,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
        complete_command(db, command, response)
        db.commit()
        return response
    except HTTPException as e:
        try:
            fail_command(db, command, str(e.detail))
        except Exception:
            pass
        db.rollback()
        raise
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/rfq/{rfq_id}/purchase-order")
def create_purchase_order(
    rfq_id: str,
    body: PurchaseOrderCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.po.create",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "rfq_id": rfq_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/rfq/{rfq_id}/purchase-order",
        user_id=user.id,
        related_id=rfq_id,
    )
    if cached:
        return cached

    try:
        po = tracking_service.create_purchase_order(
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
        response = tracking_service._serialize_po(po)  # internal serialization used by context
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/purchase-orders/{po_id}/confirm")
def confirm_purchase_order(
    po_id: str,
    body: PurchaseOrderConfirmRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.po.confirm",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "po_id": po_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/purchase-orders/{po_id}/confirm",
        user_id=user.id,
        related_id=po_id,
    )
    if cached:
        return cached

    try:
        po = tracking_service.confirm_purchase_order(
            db=db,
            po_id=po_id,
            user=user,
            vendor_confirmation_number=body.vendor_confirmation_number,
            notes=body.notes,
        )
        response = tracking_service._serialize_po(po)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/purchase-orders/{po_id}/shipments")
def create_shipment(
    po_id: str,
    body: ShipmentCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.shipment.create",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "po_id": po_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/purchase-orders/{po_id}/shipments",
        user_id=user.id,
        related_id=po_id,
    )
    if cached:
        return cached

    try:
        shipment = tracking_service.create_shipment(
            db=db,
            po_id=po_id,
            user=user,
            carrier_name=body.carrier_name,
            carrier_code=body.carrier_code,
            tracking_number=body.tracking_number,
            status=body.status,
            eta=body.eta,
            origin=body.origin,
            destination=body.destination,
            delay_reason=body.delay_reason,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_shipment(shipment)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/events")
def add_shipment_event(
    shipment_id: str,
    body: ShipmentEventCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.shipment.event",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "shipment_id": shipment_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/shipments/{shipment_id}/events",
        user_id=user.id,
        related_id=shipment_id,
    )
    if cached:
        return cached

    try:
        event = tracking_service.add_shipment_event(
            db=db,
            shipment_id=shipment_id,
            user=user,
            event_type=body.event_type,
            event_status=body.event_status,
            location=body.location,
            message=body.message,
            occurred_at=body.occurred_at,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_shipment_event(event)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/milestones")
def add_carrier_milestone(
    shipment_id: str,
    body: CarrierMilestoneCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.shipment.milestone",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "shipment_id": shipment_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/shipments/{shipment_id}/milestones",
        user_id=user.id,
        related_id=shipment_id,
    )
    if cached:
        return cached

    try:
        milestone = tracking_service.add_carrier_milestone(
            db=db,
            shipment_id=shipment_id,
            user=user,
            milestone_code=body.milestone_code,
            milestone_name=body.milestone_name,
            milestone_status=body.milestone_status,
            description=body.description,
            location=body.location,
            estimated_at=body.estimated_at,
            actual_at=body.actual_at,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_milestone(milestone)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/shipments/{shipment_id}/customs")
def add_customs_event(
    shipment_id: str,
    body: CustomsEventCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.shipment.customs",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "shipment_id": shipment_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/shipments/{shipment_id}/customs",
        user_id=user.id,
        related_id=shipment_id,
    )
    if cached:
        return cached

    try:
        customs = tracking_service.add_customs_event(
            db=db,
            shipment_id=shipment_id,
            user=user,
            country=body.country,
            status=body.status,
            message=body.message,
            held_reason=body.held_reason,
            released_at=body.released_at,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_customs(customs)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/purchase-orders/{po_id}/receipts")
def confirm_goods_receipt(
    po_id: str,
    body: GoodsReceiptCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.receipt.confirm",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "po_id": po_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/purchase-orders/{po_id}/receipts",
        user_id=user.id,
        related_id=po_id,
    )
    if cached:
        return cached

    try:
        receipt = tracking_service.confirm_goods_receipt(
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
        response = tracking_service._serialize_receipt(receipt)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/purchase-orders/{po_id}/invoices")
def create_invoice(
    po_id: str,
    body: InvoiceCreateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.invoice.create",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "po_id": po_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/purchase-orders/{po_id}/invoices",
        user_id=user.id,
        related_id=po_id,
    )
    if cached:
        return cached

    try:
        invoice = tracking_service.create_invoice(
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
            matched_at=body.matched_at,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_invoice(invoice)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/invoices/{invoice_id}/payment")
def update_payment_state(
    invoice_id: str,
    body: PaymentStateUpdateRequest,
    user: User = Depends(require_roles("manager", "buyer", "sourcing", "admin")),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="tracking.payment.update",
        idempotency_key=idempotency_key,
        payload={**body.model_dump(mode="json"), "invoice_id": invoice_id, "user_id": user.id},
        request_method="POST",
        request_path=f"/api/v1/tracking/invoices/{invoice_id}/payment",
        user_id=user.id,
        related_id=invoice_id,
    )
    if cached:
        return cached

    try:
        payment = tracking_service.update_payment_state(
            db=db,
            invoice_id=invoice_id,
            user=user,
            status=body.status,
            paid_at=body.paid_at,
            payment_reference=body.payment_reference,
            notes=body.notes,
            metadata=body.metadata,
        )
        response = tracking_service._serialize_payment_state(payment)
        complete_command(db, command, response)
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.post("/rfq/{rfq_id}/feedback")
def submit_feedback(
    rfq_id: str,
    body,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    payload = {
        "actual_cost": body.actual_cost,
        "actual_lead_time": body.actual_lead_time,
        "feedback_notes": body.feedback_notes or "",
        "user_id": user.id,
    }

    command, cached = begin_command(
        db,
        namespace="tracking.feedback.submit",
        idempotency_key=idempotency_key,
        payload={**payload, "rfq_id": rfq_id},
        request_method="POST",
        request_path=f"/api/v1/tracking/rfq/{rfq_id}/feedback",
        user_id=user.id,
        related_id=rfq_id,
    )
    if cached:
        return cached

    try:
        fb = tracking_service.submit_feedback(
            db,
            rfq_id,
            actual_cost=body.actual_cost,
            actual_lead_time=body.actual_lead_time,
            feedback_notes=body.feedback_notes or "",
        )
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))

    response = {
        "rfq_id": rfq_id,
        "cost_delta": fb.cost_delta,
        "lead_time_delta": fb.lead_time_delta,
        "status": "feedback_recorded",
    }
    complete_command(db, command, response)
    db.commit()
    return response