"""Tracking Service — legacy stage progression + fulfillment execution layer."""
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

from app.models.tracking import (
    ProductionTracking,
    ExecutionFeedback,
    TrackingStage,
    FulfillmentState,
    PurchaseOrder,
    Shipment,
    ShipmentEvent,
    CarrierMilestone,
    CustomsEvent,
    GoodsReceipt,
    Invoice,
    PaymentState,
)
from app.models.rfq import RFQBatch as RFQ, RFQStatus
from app.models.analysis import AnalysisResult
from app.models.memory import SupplierMemory
from app.models.user import User
from app.services import project_service
from app.services import analytics_service, memory_service

logger = logging.getLogger("tracking_service")

STAGE_PROGRESS = {
    TrackingStage.T0.value: 0,
    TrackingStage.T1.value: 25,
    TrackingStage.T2.value: 50,
    TrackingStage.T3.value: 75,
    TrackingStage.T4.value: 100,
}

STAGE_MESSAGES = {
    TrackingStage.T0.value: "Order placed — awaiting confirmation",
    TrackingStage.T1.value: "Material procurement in progress",
    TrackingStage.T2.value: "Manufacturing started",
    TrackingStage.T3.value: "Quality check / inspection",
    TrackingStage.T4.value: "Shipped — delivery in transit",
}

FULFILLMENT_FLOW = [
    FulfillmentState.rfq_sent.value,
    FulfillmentState.quote_received.value,
    FulfillmentState.quote_accepted.value,
    FulfillmentState.po_issued.value,
    FulfillmentState.order_confirmed.value,
    FulfillmentState.production_started.value,
    FulfillmentState.qc_passed.value,
    FulfillmentState.shipped.value,
    FulfillmentState.in_transit.value,
    FulfillmentState.customs.value,
    FulfillmentState.delivered.value,
    FulfillmentState.receipt_confirmed.value,
    FulfillmentState.invoice_matched.value,
    FulfillmentState.paid.value,
    FulfillmentState.closed.value,
]


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _uuid() -> str:
    return str(uuid.uuid4())


def _record_event_safe(db: Session, project, event_type: str, old_status: Optional[str], new_status: Optional[str], payload: Dict[str, Any], actor_user_id: Optional[str] = None):
    try:
        if hasattr(project_service, "record_project_event"):
            project_service.record_project_event(
                db=db,
                project=project,
                event_type=event_type,
                old_status=old_status,
                new_status=new_status,
                payload=payload or {},
                actor_user_id=actor_user_id,
            )
    except Exception as e:
        logger.warning("Project event record failed (non-fatal): %s", e)


def _normalize_po_number(po_number: Optional[str], rfq_id: str) -> str:
    if po_number:
        return po_number.strip()
    return f"PO-{rfq_id[:8].upper()}"


def _normalize_receipt_number(receipt_number: Optional[str], po_id: str) -> str:
    if receipt_number:
        return receipt_number.strip()
    return f"GRN-{po_id[:8].upper()}"


def _normalize_shipment_number(shipment_number: Optional[str], po_id: str) -> str:
    if shipment_number:
        return shipment_number.strip()
    return f"SHP-{po_id[:8].upper()}"


def _serialize_tracking_row(row: ProductionTracking) -> Dict[str, Any]:
    return {
        "id": row.id,
        "rfq_id": row.rfq_id,
        "stage": row.stage,
        "execution_state": row.execution_state,
        "status_message": row.status_message,
        "progress_percent": row.progress_percent,
        "po_id": row.po_id,
        "shipment_id": row.shipment_id,
        "invoice_id": row.invoice_id,
        "delay_reason": row.delay_reason,
        "context_json": row.context_json or {},
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_po(po: PurchaseOrder) -> Dict[str, Any]:
    return {
        "id": po.id,
        "project_id": po.project_id,
        "rfq_id": po.rfq_id,
        "vendor_id": po.vendor_id,
        "po_number": po.po_number,
        "status": po.status,
        "vendor_confirmation_status": po.vendor_confirmation_status,
        "vendor_confirmation_number": po.vendor_confirmation_number,
        "issued_at": po.issued_at.isoformat() if po.issued_at else None,
        "confirmed_at": po.confirmed_at.isoformat() if po.confirmed_at else None,
        "confirmed_by_user_id": po.confirmed_by_user_id,
        "currency": po.currency,
        "subtotal": _safe_float(po.subtotal),
        "freight": _safe_float(po.freight),
        "taxes": _safe_float(po.taxes),
        "total_amount": _safe_float(po.total_amount),
        "notes": po.notes,
        "metadata": po.metadata_ or {},
        "shipments": [_serialize_shipment(s) for s in po.shipments],
        "goods_receipts": [_serialize_receipt(r) for r in po.goods_receipts],
        "invoices": [_serialize_invoice(i) for i in po.invoices],
    }


def _serialize_shipment(shipment: Shipment) -> Dict[str, Any]:
    return {
        "id": shipment.id,
        "purchase_order_id": shipment.purchase_order_id,
        "shipment_number": shipment.shipment_number,
        "carrier_name": shipment.carrier_name,
        "carrier_code": shipment.carrier_code,
        "tracking_number": shipment.tracking_number,
        "status": shipment.status,
        "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        "eta": shipment.eta.isoformat() if shipment.eta else None,
        "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
        "delay_reason": shipment.delay_reason,
        "origin": shipment.origin,
        "destination": shipment.destination,
        "metadata": shipment.metadata_ or {},
    }


def _serialize_shipment_event(event: ShipmentEvent) -> Dict[str, Any]:
    return {
        "id": event.id,
        "shipment_id": event.shipment_id,
        "event_type": event.event_type,
        "event_status": event.event_status,
        "location": event.location,
        "message": event.message,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "metadata": event.metadata_ or {},
    }


def _serialize_milestone(m: CarrierMilestone) -> Dict[str, Any]:
    return {
        "id": m.id,
        "shipment_id": m.shipment_id,
        "milestone_code": m.milestone_code,
        "milestone_name": m.milestone_name,
        "milestone_status": m.milestone_status,
        "description": m.description,
        "location": m.location,
        "estimated_at": m.estimated_at.isoformat() if m.estimated_at else None,
        "actual_at": m.actual_at.isoformat() if m.actual_at else None,
        "metadata": m.metadata_ or {},
    }


def _serialize_customs(c: CustomsEvent) -> Dict[str, Any]:
    return {
        "id": c.id,
        "shipment_id": c.shipment_id,
        "country": c.country,
        "status": c.status,
        "message": c.message,
        "held_reason": c.held_reason,
        "released_at": c.released_at.isoformat() if c.released_at else None,
        "metadata": c.metadata_ or {},
    }


def _serialize_receipt(r: GoodsReceipt) -> Dict[str, Any]:
    return {
        "id": r.id,
        "purchase_order_id": r.purchase_order_id,
        "shipment_id": r.shipment_id,
        "receipt_number": r.receipt_number,
        "receipt_status": r.receipt_status,
        "received_quantity": _safe_float(r.received_quantity),
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "confirmed_by_user_id": r.confirmed_by_user_id,
        "notes": r.notes,
        "metadata": r.metadata_ or {},
    }


def _serialize_invoice(i: Invoice) -> Dict[str, Any]:
    payment_state = i.payment_state
    return {
        "id": i.id,
        "purchase_order_id": i.purchase_order_id,
        "vendor_id": i.vendor_id,
        "invoice_number": i.invoice_number,
        "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
        "due_date": i.due_date.isoformat() if i.due_date else None,
        "invoice_status": i.invoice_status,
        "currency": i.currency,
        "subtotal": _safe_float(i.subtotal),
        "taxes": _safe_float(i.taxes),
        "total_amount": _safe_float(i.total_amount),
        "matched_at": i.matched_at.isoformat() if i.matched_at else None,
        "metadata": i.metadata_ or {},
        "payment_state": _serialize_payment_state(payment_state) if payment_state else None,
    }


def _serialize_payment_state(p: PaymentState) -> Dict[str, Any]:
    return {
        "id": p.id,
        "invoice_id": p.invoice_id,
        "purchase_order_id": p.purchase_order_id,
        "status": p.status,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        "payment_reference": p.payment_reference,
        "notes": p.notes,
        "metadata": p.metadata_ or {},
    }


def _derive_execution_state(db: Session, rfq_id: str) -> str:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return FulfillmentState.rfq_sent.value

    po = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.rfq_id == rfq_id)
        .order_by(PurchaseOrder.created_at.desc())
        .first()
    )
    if not po:
        if rfq.status in ("draft", "sent"):
            return FulfillmentState.rfq_sent.value
        if rfq.status == "quoted":
            return FulfillmentState.quote_received.value
        if rfq.status == "approved":
            return FulfillmentState.quote_accepted.value
        return FulfillmentState.rfq_sent.value

    if po.status in (FulfillmentState.closed.value, "closed"):
        return FulfillmentState.closed.value
    if po.status in (FulfillmentState.paid.value, "paid"):
        return FulfillmentState.paid.value

    payment = (
        db.query(PaymentState)
        .join(Invoice, Invoice.id == PaymentState.invoice_id)
        .filter(PaymentState.purchase_order_id == po.id)
        .order_by(PaymentState.created_at.desc())
        .first()
    )
    if payment:
        if payment.status == "paid":
            return FulfillmentState.paid.value
        if payment.status == "matched":
            return FulfillmentState.invoice_matched.value

    invoice = (
        db.query(Invoice)
        .filter(Invoice.purchase_order_id == po.id)
        .order_by(Invoice.created_at.desc())
        .first()
    )
    if invoice:
        if invoice.invoice_status in ("matched", "reconciled"):
            return FulfillmentState.invoice_matched.value
        if invoice.invoice_status in ("issued", "sent"):
            return FulfillmentState.receipt_confirmed.value if _has_goods_receipt(db, po.id) else FulfillmentState.invoice_matched.value

    receipt = (
        db.query(GoodsReceipt)
        .filter(GoodsReceipt.purchase_order_id == po.id)
        .order_by(GoodsReceipt.created_at.desc())
        .first()
    )
    if receipt and receipt.receipt_status in ("confirmed", "received", "accepted"):
        return FulfillmentState.receipt_confirmed.value

    shipment = (
        db.query(Shipment)
        .filter(Shipment.purchase_order_id == po.id)
        .order_by(Shipment.created_at.desc())
        .first()
    )
    if shipment:
        if shipment.delivered_at:
            return FulfillmentState.delivered.value
        if shipment.status in ("customs",):
            return FulfillmentState.customs.value
        if shipment.status in ("in_transit",):
            return FulfillmentState.in_transit.value
        if shipment.status in ("shipped",):
            return FulfillmentState.shipped.value

    if po.confirmed_at:
        return FulfillmentState.order_confirmed.value

    return FulfillmentState.po_issued.value


def _has_goods_receipt(db: Session, po_id: str) -> bool:
    return (
        db.query(GoodsReceipt)
        .filter(GoodsReceipt.purchase_order_id == po_id)
        .count()
        > 0
    )


def _build_timeline(db: Session, rfq_id: str) -> List[Dict[str, Any]]:
    rows = (
        db.query(ProductionTracking)
        .filter(ProductionTracking.rfq_id == rfq_id)
        .order_by(ProductionTracking.created_at.asc())
        .all()
    )
    timeline = [_serialize_tracking_row(r) for r in rows]

    po = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.rfq_id == rfq_id)
        .order_by(PurchaseOrder.created_at.desc())
        .first()
    )
    if po:
        timeline.append({
            "type": "purchase_order",
            "id": po.id,
            "label": "Purchase order issued",
            "status": po.status,
            "po_number": po.po_number,
            "vendor_confirmation_status": po.vendor_confirmation_status,
            "vendor_confirmation_number": po.vendor_confirmation_number,
            "issued_at": po.issued_at.isoformat() if po.issued_at else None,
            "confirmed_at": po.confirmed_at.isoformat() if po.confirmed_at else None,
            "vendor_id": po.vendor_id,
            "currency": po.currency,
            "total_amount": _safe_float(po.total_amount),
        })

        shipments = (
            db.query(Shipment)
            .filter(Shipment.purchase_order_id == po.id)
            .order_by(Shipment.created_at.asc())
            .all()
        )
        for shipment in shipments:
            timeline.append({
                "type": "shipment",
                "id": shipment.id,
                "label": "Shipment",
                "shipment_number": shipment.shipment_number,
                "carrier_name": shipment.carrier_name,
                "tracking_number": shipment.tracking_number,
                "status": shipment.status,
                "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
                "eta": shipment.eta.isoformat() if shipment.eta else None,
                "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
                "delay_reason": shipment.delay_reason,
                "origin": shipment.origin,
                "destination": shipment.destination,
            })
            for evt in shipment.events:
                timeline.append({
                    "type": "shipment_event",
                    "id": evt.id,
                    "shipment_id": evt.shipment_id,
                    "event_type": evt.event_type,
                    "event_status": evt.event_status,
                    "location": evt.location,
                    "message": evt.message,
                    "occurred_at": evt.occurred_at.isoformat() if evt.occurred_at else None,
                })
            for ms in shipment.milestones:
                timeline.append({
                    "type": "carrier_milestone",
                    "id": ms.id,
                    "shipment_id": ms.shipment_id,
                    "milestone_code": ms.milestone_code,
                    "milestone_name": ms.milestone_name,
                    "milestone_status": ms.milestone_status,
                    "description": ms.description,
                    "location": ms.location,
                    "estimated_at": ms.estimated_at.isoformat() if ms.estimated_at else None,
                    "actual_at": ms.actual_at.isoformat() if ms.actual_at else None,
                })
            for c in shipment.customs_events:
                timeline.append({
                    "type": "customs_event",
                    "id": c.id,
                    "shipment_id": c.shipment_id,
                    "country": c.country,
                    "status": c.status,
                    "message": c.message,
                    "held_reason": c.held_reason,
                    "released_at": c.released_at.isoformat() if c.released_at else None,
                })
            for r in shipment.receipts:
                timeline.append({
                    "type": "goods_receipt",
                    "id": r.id,
                    "purchase_order_id": r.purchase_order_id,
                    "shipment_id": r.shipment_id,
                    "receipt_number": r.receipt_number,
                    "receipt_status": r.receipt_status,
                    "received_quantity": _safe_float(r.received_quantity),
                    "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
                    "notes": r.notes,
                })

        for inv in po.invoices:
            timeline.append({
                "type": "invoice",
                "id": inv.id,
                "purchase_order_id": inv.purchase_order_id,
                "vendor_id": inv.vendor_id,
                "invoice_number": inv.invoice_number,
                "invoice_status": inv.invoice_status,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "matched_at": inv.matched_at.isoformat() if inv.matched_at else None,
                "total_amount": _safe_float(inv.total_amount),
                "payment_status": inv.payment_state.status if inv.payment_state else None,
                "paid_at": inv.payment_state.paid_at.isoformat() if inv.payment_state and inv.payment_state.paid_at else None,
            })
    return timeline


def _update_project_from_fulfillment(db: Session, rfq: RFQ, state: str, pointer_payload: Dict[str, Any], actor: Optional[str] = None):
    project = None
    if rfq and rfq.bom_id:
        project = db.query(__import__("app.models.project", fromlist=["Project"]).Project).filter(
            __import__("app.models.project", fromlist=["Project"]).Project.bom_id == rfq.bom_id
        ).first()

    if not project:
        return None

    old_status = getattr(project, "workflow_stage", None) or getattr(project, "status", None)
    project.workflow_stage = state
    project.status = state
    project.rfq_status = rfq.status if rfq else getattr(project, "rfq_status", "none")
    project.tracking_stage = pointer_payload.get("tracking_stage", getattr(project, "tracking_stage", "init"))
    project.current_rfq_id = rfq.id if rfq else getattr(project, "current_rfq_id", None)
    project.current_po_id = pointer_payload.get("po_id", getattr(project, "current_po_id", None))
    project.current_shipment_id = pointer_payload.get("shipment_id", getattr(project, "current_shipment_id", None))
    project.current_invoice_id = pointer_payload.get("invoice_id", getattr(project, "current_invoice_id", None))

    vendor_id = pointer_payload.get("vendor_id")
    if vendor_id:
        project.current_vendor_id = vendor_id

    project.project_metadata = project.project_metadata or {}
    project.project_metadata["workflow_stage"] = state
    project.project_metadata["next_action"] = _next_action_for_state(state)
    project.project_metadata["fulfillment_state"] = state
    project.project_metadata["fulfillment_pointer"] = pointer_payload
    project.project_metadata["current_rfq_id"] = rfq.id if rfq else project.project_metadata.get("current_rfq_id")
    if vendor_id:
        project.project_metadata["current_vendor_id"] = vendor_id
        project.project_metadata["selected_vendor_id"] = vendor_id
    _record_event_safe(
        db,
        project,
        f"fulfillment_{state}",
        old_status,
        state,
        pointer_payload,
        actor_user_id=actor,
    )
    db.flush()
    return project


def _next_action_for_state(state: str) -> str:
    mapping = {
        FulfillmentState.rfq_sent.value: "Collect quotes",
        FulfillmentState.quote_received.value: "Compare quotes",
        FulfillmentState.quote_accepted.value: "Issue PO",
        FulfillmentState.po_issued.value: "Confirm order",
        FulfillmentState.order_confirmed.value: "Start production",
        FulfillmentState.production_started.value: "Monitor build progress",
        FulfillmentState.qc_passed.value: "Await shipment",
        FulfillmentState.shipped.value: "Track transit",
        FulfillmentState.in_transit.value: "Watch customs / arrival",
        FulfillmentState.customs.value: "Resolve customs hold",
        FulfillmentState.delivered.value: "Confirm receipt",
        FulfillmentState.receipt_confirmed.value: "Match invoice",
        FulfillmentState.invoice_matched.value: "Capture payment",
        FulfillmentState.paid.value: "Close project",
        FulfillmentState.closed.value: "Closed",
    }
    return mapping.get(state, "Review project")


def create_tracking(db: Session, rfq_id: str) -> ProductionTracking:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    exec_state = _derive_execution_state(db, rfq_id)

    tracking = ProductionTracking(
        rfq_id=rfq_id,
        stage=TrackingStage.T0.value,
        execution_state=exec_state,
        status_message=STAGE_MESSAGES[TrackingStage.T0.value],
        progress_percent=0,
        context_json={"execution_state": exec_state, "source": "create_tracking"},
    )
    db.add(tracking)
    if rfq:
        rfq.status = RFQStatus.in_production.value if hasattr(RFQStatus, "in_production") else rfq.status
    db.flush()
    db.refresh(tracking)
    return tracking


def advance_stage(db: Session, rfq_id: str, updated_by: str = "system") -> Optional[ProductionTracking]:
    tracking = (
        db.query(ProductionTracking)
        .filter(ProductionTracking.rfq_id == rfq_id)
        .order_by(ProductionTracking.created_at.desc())
        .first()
    )
    if not tracking:
        return create_tracking(db, rfq_id)

    stages = list(STAGE_PROGRESS.keys())
    current_idx = stages.index(tracking.stage) if tracking.stage in stages else 0

    if current_idx >= len(stages) - 1:
        rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
        if rfq:
            rfq.status = RFQStatus.completed.value if hasattr(RFQStatus, "completed") else rfq.status
            project = project_service.sync_project_completion(db, rfq) if hasattr(project_service, "sync_project_completion") else None
            _update_project_from_fulfillment(
                db,
                rfq,
                FulfillmentState.closed.value,
                {"tracking_stage": TrackingStage.T4.value},
                actor=updated_by,
            )
        return tracking

    next_stage = stages[current_idx + 1]
    exec_state = _derive_execution_state(db, rfq_id)

    new_tracking = ProductionTracking(
        rfq_id=rfq_id,
        stage=next_stage,
        execution_state=exec_state,
        status_message=STAGE_MESSAGES.get(next_stage, ""),
        progress_percent=STAGE_PROGRESS.get(next_stage, 0),
        updated_by=updated_by,
        context_json={"legacy_stage": next_stage, "execution_state": exec_state},
    )
    db.add(new_tracking)
    db.flush()
    db.refresh(new_tracking)
    return new_tracking


def get_tracking(db: Session, rfq_id: str) -> List[ProductionTracking]:
    return (
        db.query(ProductionTracking)
        .filter(ProductionTracking.rfq_id == rfq_id)
        .order_by(ProductionTracking.created_at.asc())
        .all()
    )


def get_fulfillment_context(db: Session, rfq_id: str) -> Dict[str, Any]:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    project = None
    if rfq.bom_id:
        project = db.query(__import__("app.models.project", fromlist=["Project"]).Project).filter(
            __import__("app.models.project", fromlist=["Project"]).Project.bom_id == rfq.bom_id
        ).first()

    po = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.rfq_id == rfq.id)
        .order_by(PurchaseOrder.created_at.desc())
        .first()
    )
    shipments = []
    shipment_events = []
    carrier_milestones = []
    customs_events = []
    goods_receipts = []
    invoices = []
    payment_state = None

    if po:
        shipments = (
            db.query(Shipment)
            .filter(Shipment.purchase_order_id == po.id)
            .order_by(Shipment.created_at.asc())
            .all()
        )
        goods_receipts = (
            db.query(GoodsReceipt)
            .filter(GoodsReceipt.purchase_order_id == po.id)
            .order_by(GoodsReceipt.created_at.asc())
            .all()
        )
        invoices = (
            db.query(Invoice)
            .filter(Invoice.purchase_order_id == po.id)
            .order_by(Invoice.created_at.asc())
            .all()
        )
        if invoices:
            payment_state = (
                db.query(PaymentState)
                .filter(PaymentState.invoice_id == invoices[-1].id)
                .first()
            )
        for sh in shipments:
            shipment_events.extend(
                db.query(ShipmentEvent)
                .filter(ShipmentEvent.shipment_id == sh.id)
                .order_by(ShipmentEvent.occurred_at.asc())
                .all()
            )
            carrier_milestones.extend(
                db.query(CarrierMilestone)
                .filter(CarrierMilestone.shipment_id == sh.id)
                .order_by(CarrierMilestone.created_at.asc())
                .all()
            )
            customs_events.extend(
                db.query(CustomsEvent)
                .filter(CustomsEvent.shipment_id == sh.id)
                .order_by(CustomsEvent.created_at.asc())
                .all()
            )

    tracking_history = get_tracking(db, rfq_id)
    execution_state = _derive_execution_state(db, rfq_id)
    timeline = _build_timeline(db, rfq_id)

    po_number = po.po_number if po else None
    vendor_confirmation = po.vendor_confirmation_number if po else None
    tracking_number = shipments[-1].tracking_number if shipments else None
    carrier_name = shipments[-1].carrier_name if shipments else None
    eta = shipments[-1].eta if shipments else None
    delay_reason = shipments[-1].delay_reason if shipments else None
    receipt_confirmation = None
    if goods_receipts:
        receipt_confirmation = goods_receipts[-1].receipt_status

    return {
        "rfq_id": rfq.id,
        "project_id": project.id if project else None,
        "rfq_status": rfq.status,
        "execution_state": execution_state,
        "next_action": _next_action_for_state(execution_state),
        "purchase_order": _serialize_po(po) if po else None,
        "shipments": [_serialize_shipment(s) for s in shipments],
        "shipment_events": [_serialize_shipment_event(e) for e in shipment_events],
        "carrier_milestones": [_serialize_milestone(m) for m in carrier_milestones],
        "customs_events": [_serialize_customs(c) for c in customs_events],
        "goods_receipts": [_serialize_receipt(r) for r in goods_receipts],
        "invoices": [_serialize_invoice(i) for i in invoices],
        "payment_state": _serialize_payment_state(payment_state) if payment_state else None,
        "tracking_history": [_serialize_tracking_row(t) for t in tracking_history],
        "timeline": timeline,
        "po_number": po_number,
        "vendor_confirmation": vendor_confirmation,
        "tracking_number": tracking_number,
        "carrier_name": carrier_name,
        "eta": eta,
        "delay_reason": delay_reason,
        "receipt_confirmation": receipt_confirmation,
    }


def create_purchase_order(
    db: Session,
    rfq_id: str,
    user: User,
    vendor_id: Optional[str] = None,
    vendor_contact_id: Optional[str] = None,
    source_quote_header_id: Optional[str] = None,
    po_number: Optional[str] = None,
    currency: Optional[str] = None,
    incoterms: Optional[str] = None,
    freight_terms: Optional[str] = None,
    payment_terms: Optional[str] = None,
    subtotal: Optional[float] = None,
    freight: Optional[float] = None,
    taxes: Optional[float] = None,
    total_amount: Optional[float] = None,
    notes: Optional[str] = None,
    purchase_terms_json: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PurchaseOrder:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    project = None
    if rfq.bom_id:
        project = db.query(__import__("app.models.project", fromlist=["Project"]).Project).filter(
            __import__("app.models.project", fromlist=["Project"]).Project.bom_id == rfq.bom_id
        ).first()

    vendor_id = vendor_id or rfq.selected_vendor_id
    po = PurchaseOrder(
        project_id=project.id if project else None,
        rfq_id=rfq.id,
        vendor_id=vendor_id,
        vendor_contact_id=vendor_contact_id,
        source_quote_header_id=source_quote_header_id,
        po_number=_normalize_po_number(po_number, rfq.id),
        status=FulfillmentState.po_issued.value,
        vendor_confirmation_status="pending",
        issued_at=datetime.utcnow(),
        currency=currency or getattr(rfq, "target_currency", None) or "USD",
        incoterms=incoterms,
        freight_terms=freight_terms,
        payment_terms=payment_terms,
        purchase_terms_json=purchase_terms_json or {},
        subtotal=subtotal if subtotal is not None else _safe_float(rfq.total_estimated_cost, None),
        freight=freight,
        taxes=taxes,
        total_amount=total_amount if total_amount is not None else _safe_float(rfq.total_final_cost, None),
        notes=notes,
        metadata_=metadata or {},
    )
    db.add(po)
    db.flush()

    analytics_service.record_purchase_order_spend(
        db,
        po,
        project=project,
        rfq=rfq,
        actor=user,
    )

    if rfq:
        rfq.status = RFQStatus.approved.value
        rfq.selected_vendor_id = vendor_id

    tracking = ProductionTracking(
        rfq_id=rfq.id,
        stage=TrackingStage.T0.value,
        execution_state=FulfillmentState.po_issued.value,
        status_message="Purchase order issued",
        progress_percent=0,
        updated_by=user.email if user else "system",
        po_id=po.id,
        context_json={"po_id": po.id, "po_number": po.po_number, "vendor_id": vendor_id},
    )
    db.add(tracking)
    db.flush()

    if project:
        _update_project_from_fulfillment(
            db,
            rfq,
            FulfillmentState.po_issued.value,
            {"po_id": po.id, "tracking_stage": TrackingStage.T0.value, "vendor_id": vendor_id},
            actor=user.id if user else None,
        )
        project.current_po_id = po.id
        project.current_vendor_id = vendor_id

    db.flush()
    db.refresh(po)
    return po


def confirm_purchase_order(
    db: Session,
    po_id: str,
    user: User,
    vendor_confirmation_number: Optional[str] = None,
    notes: Optional[str] = None,
) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"Purchase order not found: {po_id}")

    po.vendor_confirmation_status = "confirmed"
    po.vendor_confirmation_number = vendor_confirmation_number
    po.confirmed_at = datetime.utcnow()
    po.confirmed_by_user_id = user.id if user else None
    if notes:
        po.notes = f"{po.notes or ''}\n{notes}".strip()

    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    tracking = ProductionTracking(
        rfq_id=po.rfq_id,
        stage=TrackingStage.T1.value,
        execution_state=FulfillmentState.order_confirmed.value,
        status_message="Vendor confirmed purchase order",
        progress_percent=25,
        updated_by=user.email if user else "system",
        po_id=po.id,
        context_json={"po_id": po.id, "vendor_confirmation_number": vendor_confirmation_number},
    )
    db.add(tracking)
    db.flush()

    if rfq:
        _update_project_from_fulfillment(
            db,
            rfq,
            FulfillmentState.order_confirmed.value,
            {"po_id": po.id, "tracking_stage": TrackingStage.T1.value},
            actor=user.id if user else None,
        )

    db.flush()
    db.refresh(po)
    return po


def create_shipment(
    db: Session,
    po_id: str,
    user: User,
    carrier_name: Optional[str] = None,
    carrier_code: Optional[str] = None,
    tracking_number: Optional[str] = None,
    tracking_number_source: Optional[str] = None,
    tracking_reference: Optional[str] = None,
    status: Optional[str] = None,
    eta: Optional[datetime] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    delay_reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Shipment:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"Purchase order not found: {po_id}")

    shipment = Shipment(
        purchase_order_id=po.id,
        shipment_number=_normalize_shipment_number(None, po.id),
        carrier_name=carrier_name,
        carrier_code=carrier_code,
        tracking_number=tracking_number,
        tracking_number_source=tracking_number_source,
        tracking_reference=tracking_reference,
        status=status or FulfillmentState.shipped.value,
        shipped_at=datetime.utcnow(),
        eta=eta,
        delay_reason=delay_reason,
        origin=origin,
        destination=destination,
        metadata_=metadata or {},
    )
    db.add(shipment)
    db.flush()

    db.add(ShipmentEvent(
        shipment_id=shipment.id,
        event_type="shipment_created",
        event_status="recorded",
        location=origin,
        message="Shipment created",
        occurred_at=datetime.utcnow(),
        metadata_={"created_by": user.email if user else "system"},
    ))
    db.add(CarrierMilestone(
        shipment_id=shipment.id,
        milestone_code="shipped",
        milestone_name="Shipped",
        milestone_status="completed",
        description="Shipment has been dispatched",
        location=origin,
        actual_at=datetime.utcnow(),
        metadata_={},
    ))
    db.flush()

    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    project = None
    if rfq and rfq.bom_id:
        Project = __import__("app.models.project", fromlist=["Project"]).Project
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()

    analytics_service.record_delivery_performance(
        db,
        shipment,
        project=project,
        rfq=rfq,
        actor=user,
    )

    if rfq:
        _update_project_from_fulfillment(
            db,
            rfq,
            FulfillmentState.shipped.value if status != "in_transit" else FulfillmentState.in_transit.value,
            {"po_id": po.id, "shipment_id": shipment.id, "tracking_stage": TrackingStage.T4.value},
            actor=user.id if user else None,
        )

    db.refresh(shipment)
    return shipment


def add_shipment_event(
    db: Session,
    shipment_id: str,
    user: User,
    event_type: str,
    event_status: Optional[str] = "recorded",
    location: Optional[str] = None,
    message: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ShipmentEvent:
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise ValueError(f"Shipment not found: {shipment_id}")

    event = ShipmentEvent(
        shipment_id=shipment.id,
        event_type=event_type,
        event_status=event_status or "recorded",
        location=location,
        message=message,
        occurred_at=occurred_at or datetime.utcnow(),
        metadata_=metadata or {},
    )
    db.add(event)
    db.flush()

    if event_type.lower() in ("in_transit", "transit", "carrier_scan"):
        shipment.status = FulfillmentState.in_transit.value
    elif event_type.lower() in ("delivered", "delivery_confirmed"):
        shipment.status = FulfillmentState.delivered.value
        shipment.delivered_at = occurred_at or datetime.utcnow()
    elif event_type.lower() in ("customs_hold", "customs"):
        shipment.status = FulfillmentState.customs.value
        shipment.delay_reason = message or shipment.delay_reason

    analytics_service.record_delivery_performance(
        db,
        shipment,
        actor=user,
    )

    db.flush()
    return event


def add_carrier_milestone(
    db: Session,
    shipment_id: str,
    user: User,
    milestone_code: str,
    milestone_name: str,
    milestone_status: Optional[str] = "pending",
    description: Optional[str] = None,
    location: Optional[str] = None,
    estimated_at: Optional[datetime] = None,
    actual_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> CarrierMilestone:
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise ValueError(f"Shipment not found: {shipment_id}")

    milestone = CarrierMilestone(
        shipment_id=shipment.id,
        milestone_code=milestone_code,
        milestone_name=milestone_name,
        milestone_status=milestone_status or "pending",
        description=description,
        location=location,
        estimated_at=estimated_at,
        actual_at=actual_at,
        metadata_=metadata or {},
    )
    db.add(milestone)
    db.flush()
    return milestone


def add_customs_event(
    db: Session,
    shipment_id: str,
    user: User,
    country: Optional[str] = None,
    status: Optional[str] = "pending",
    message: Optional[str] = None,
    held_reason: Optional[str] = None,
    released_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> CustomsEvent:
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise ValueError(f"Shipment not found: {shipment_id}")

    customs = CustomsEvent(
        shipment_id=shipment.id,
        country=country,
        status=status or "pending",
        message=message,
        held_reason=held_reason,
        released_at=released_at,
        metadata_=metadata or {},
    )
    db.add(customs)
    db.flush()

    shipment.status = FulfillmentState.customs.value if (status or "").lower() != "released" else FulfillmentState.in_transit.value
    if held_reason:
        shipment.delay_reason = held_reason
    db.flush()
    return customs


def confirm_goods_receipt(
    db: Session,
    po_id: str,
    user: User,
    shipment_id: Optional[str] = None,
    receipt_number: Optional[str] = None,
    receipt_status: Optional[str] = "confirmed",
    received_quantity: Optional[float] = None,
    confirmed_at: Optional[datetime] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> GoodsReceipt:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"Purchase order not found: {po_id}")

    receipt = GoodsReceipt(
        purchase_order_id=po.id,
        shipment_id=shipment_id,
        receipt_number=_normalize_receipt_number(receipt_number, po.id),
        receipt_status=receipt_status or "confirmed",
        received_quantity=received_quantity,
        confirmed_at=confirmed_at or datetime.utcnow(),
        confirmed_by_user_id=user.id if user else None,
        notes=notes,
        metadata_=metadata or {},
    )
    db.add(receipt)
    db.flush()

    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    if rfq:
        _update_project_from_fulfillment(
            db,
            rfq,
            FulfillmentState.receipt_confirmed.value,
            {"po_id": po.id, "shipment_id": shipment_id, "tracking_stage": TrackingStage.T4.value},
            actor=user.id if user else None,
        )

    if shipment_id:
        shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
        if shipment:
            project = None
            if rfq and rfq.bom_id:
                Project = __import__("app.models.project", fromlist=["Project"]).Project
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
            analytics_service.record_delivery_performance(
                db,
                shipment,
                project=project,
                rfq=rfq,
                actor=user,
            )

    if po and po.vendor_id:
        memory_service.record_delivery_outcome(
            db,
            vendor_id=po.vendor_id,
            on_time=True if shipment_id else None,
            delay_days=None,
            spend_amount=_safe_float(po.total_amount, None),
        )

    db.flush()
    return receipt


def create_invoice(
    db: Session,
    po_id: str,
    user: User,
    vendor_id: Optional[str] = None,
    invoice_number: Optional[str] = None,
    invoice_date: Optional[datetime] = None,
    due_date: Optional[datetime] = None,
    invoice_status: Optional[str] = "issued",
    currency: Optional[str] = None,
    subtotal: Optional[float] = None,
    taxes: Optional[float] = None,
    total_amount: Optional[float] = None,
    matched_at: Optional[datetime] = None,
    payment_provider: Optional[str] = None,
    payment_provider_reference: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Invoice:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"Purchase order not found: {po_id}")

    rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
    project = None
    if rfq and rfq.bom_id:
        Project = __import__("app.models.project", fromlist=["Project"]).Project
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()

    inv = Invoice(
        purchase_order_id=po.id,
        vendor_id=vendor_id or po.vendor_id,
        invoice_number=invoice_number or f"INV-{po.po_number}",
        invoice_date=invoice_date or datetime.utcnow(),
        due_date=due_date,
        invoice_status=invoice_status or "issued",
        currency=currency or po.currency or "USD",
        subtotal=subtotal,
        taxes=taxes,
        total_amount=total_amount,
        matched_at=matched_at,
        payment_provider=payment_provider,
        payment_provider_reference=payment_provider_reference,
        metadata_=metadata or {},
    )
    db.add(inv)
    db.flush()

    analytics_service.record_invoice_spend(
        db,
        inv,
        po=po,
        project=project,
        rfq=rfq,
        actor=user,
    )

    po_row_tracking = ProductionTracking(
        rfq_id=po.rfq_id,
        stage=TrackingStage.T4.value,
        execution_state=FulfillmentState.invoice_matched.value,
        status_message="Invoice issued / matched",
        progress_percent=95,
        updated_by=user.email if user else "system",
        po_id=po.id,
        invoice_id=inv.id,
        context_json={"po_id": po.id, "invoice_id": inv.id, "invoice_number": inv.invoice_number},
    )
    db.add(po_row_tracking)
    db.flush()
    return inv


def update_payment_state(
    db: Session,
    invoice_id: str,
    user: User,
    status: str,
    paid_at: Optional[datetime] = None,
    payment_reference: Optional[str] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PaymentState:
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise ValueError(f"Invoice not found: {invoice_id}")

    payment = db.query(PaymentState).filter(PaymentState.invoice_id == inv.id).first()
    if not payment:
        payment = PaymentState(
            invoice_id=inv.id,
            purchase_order_id=inv.purchase_order_id,
            status=status,
            paid_at=paid_at,
            payment_reference=payment_reference,
            notes=notes,
            metadata_=metadata or {},
        )
        db.add(payment)
    else:
        payment.status = status
        payment.paid_at = paid_at or payment.paid_at
        payment.payment_reference = payment_reference or payment.payment_reference
        payment.notes = notes or payment.notes
        payment.metadata_ = metadata or payment.metadata_ or {}
        payment.updated_at = datetime.utcnow()

    db.flush()
    inv.invoice_status = "paid" if status == "paid" else inv.invoice_status
    if status == "paid":
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == inv.purchase_order_id).first()
        if po:
            po.status = FulfillmentState.closed.value
            rfq = db.query(RFQ).filter(RFQ.id == po.rfq_id).first()
            project = None
            if rfq and rfq.bom_id:
                Project = __import__("app.models.project", fromlist=["Project"]).Project
                project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
            analytics_service.record_payment_spend(
                db,
                payment,
                inv,
                po=po,
                project=project,
                rfq=rfq,
                actor=user,
            )
            if rfq:
                _update_project_from_fulfillment(
                    db,
                    rfq,
                    FulfillmentState.closed.value,
                    {"po_id": po.id, "invoice_id": inv.id, "tracking_stage": TrackingStage.T4.value},
                    actor=user.id if user else None,
                )
    db.flush()
    return payment


def submit_feedback(
    db: Session,
    rfq_id: str,
    actual_cost: Optional[float] = None,
    actual_lead_time: Optional[float] = None,
    feedback_notes: str = "",
) -> ExecutionFeedback:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    predicted_cost = rfq.total_estimated_cost or 0
    predicted_lead = _get_predicted_lead_time(db, rfq_id)

    cost_delta = (actual_cost - predicted_cost) if actual_cost is not None else None
    lead_delta = (actual_lead_time - predicted_lead) if actual_lead_time is not None else None

    fb = ExecutionFeedback(
        rfq_id=rfq_id,
        predicted_cost=predicted_cost,
        actual_cost=actual_cost,
        cost_delta=cost_delta,
        predicted_lead_time=predicted_lead,
        actual_lead_time=actual_lead_time,
        lead_time_delta=lead_delta,
        feedback_notes=feedback_notes,
    )
    db.add(fb)

    if rfq.selected_vendor_id:
        _update_memory(db, rfq.selected_vendor_id, cost_delta, lead_delta)

    db.flush()
    db.refresh(fb)
    logger.info("Feedback recorded for RFQ %s: cost_delta=%s, lead_delta=%s", rfq_id, cost_delta, lead_delta)
    return fb


def get_rfq_for_feedback(db: Session, rfq_id: str) -> Optional[RFQ]:
    return db.query(RFQ).filter(RFQ.id == rfq_id).first()


def _get_predicted_lead_time(db: Session, rfq_id: str) -> float:
    """FIXED: Get predicted lead time from analysis/strategy, not hardcoded 14."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if rfq and rfq.bom_id:
        analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == rfq.bom_id).first()
        if analysis and analysis.lead_time:
            return float(analysis.lead_time)
        if analysis and analysis.strategy_output:
            rec = analysis.strategy_output.get("recommended_strategy", {})
            if rec.get("lead_time"):
                return float(rec["lead_time"])
    return 14.0  # fallback only if no analysis data exists


def _update_memory(db: Session, vendor_id: str, cost_delta: Optional[float], lead_delta: Optional[float]):
    mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor_id).first()
    if not mem:
        return

    mem.total_orders = (mem.total_orders or 0) + 1
    n = mem.total_orders

    if cost_delta is not None:
        cost_pct = cost_delta / max(abs(cost_delta) + 100, 1) * 100
        mem.avg_cost_delta_pct = ((mem.avg_cost_delta_pct or 0) * (n - 1) + cost_pct) / n
        mem.cost_accuracy_score = max(0, min(1, 1.0 - abs(mem.avg_cost_delta_pct) / 50))

    if lead_delta is not None:
        mem.avg_lead_delta_days = ((mem.avg_lead_delta_days or 0) * (n - 1) + lead_delta) / n
        mem.delivery_accuracy_score = max(0, min(1, 1.0 - abs(mem.avg_lead_delta_days) / 14))

    mem.performance_score = (mem.cost_accuracy_score + mem.delivery_accuracy_score) / 2
    risk_score = max(0, min(1, 1.0 - mem.performance_score))
    mem.risk_level = "low" if risk_score < 0.2 else ("high" if risk_score > 0.5 else "medium")
