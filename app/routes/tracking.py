"""Tracking routes — order center, fulfillment context, shipments, receipts, invoices, payments."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.project import Project
from app.models.rfq import RFQ
from app.utils.dependencies import require_user, build_project_access_context, can_access_project
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


def _require_rfq_project_access(db: Session, rfq_id: str, user: User) -> None:
    context = tracking_service.get_fulfillment_context(db, rfq_id)
    project_id = context.get("project_id")
    if not project_id:
        return
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_access_project(user, project):
        raise HTTPException(status_code=403, detail="Not authorized")


@router.get("/rfq/{rfq_id}", response_model=FulfillmentContextResponse)
def get_tracking(rfq_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Returns the full fulfillment context, not just stage history."""
    try:
        _require_rfq_project_access(db, rfq_id, user)
        response = tracking_service.get_fulfillment_context(db, rfq_id)
        project = None
        if response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        if not project:
            rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        response["access"] = build_project_access_context(user, project, db)
        return response
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

    _require_rfq_project_access(db, rfq_id, user)

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
        project = None
        if entry and getattr(entry, "rfq_id", None):
            rfq = db.query(RFQ).filter(RFQ.id == entry.rfq_id).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        response["access"] = build_project_access_context(user, project, db)
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

    _require_rfq_project_access(db, rfq_id, user)

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
        project = None
        if entry and getattr(entry, "rfq_id", None):
            rfq = db.query(RFQ).filter(RFQ.id == entry.rfq_id).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    _require_rfq_project_access(db, rfq_id, user)

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
        project = db.query(Project).filter(Project.id == po.project_id).first() if po.project_id else None
        response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

    try:
        po = tracking_service.confirm_purchase_order(
            db=db,
            po_id=po_id,
            user=user,
            vendor_confirmation_number=body.vendor_confirmation_number,
            notes=body.notes,
        )
        response = tracking_service._serialize_po(po)
        project = db.query(Project).filter(Project.id == po.project_id).first() if po.project_id else None
        response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.purchase_order_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.purchase_order_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.purchase_order_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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
    user: User = Depends(require_user),
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

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == inv.purchase_order_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq and rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if project and not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Not authorized")

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
        project = None
        if hasattr(response, "get") and response.get("project_id"):
            project = db.query(Project).filter(Project.id == response["project_id"]).first()
        elif hasattr(response, "get") and response.get("rfq_id"):
            rfq = db.query(RFQ).filter(RFQ.id == response["rfq_id"]).first()
            if rfq and rfq.bom_id:
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
        if hasattr(response, "__setitem__"):
            response["access"] = build_project_access_context(user, project, db)
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