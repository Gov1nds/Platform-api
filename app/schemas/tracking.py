"""Tracking / fulfillment schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TrackingEntrySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rfq_id: str
    stage: str
    execution_state: Optional[str] = None
    status_message: Optional[str] = None
    progress_percent: int = 0
    po_id: Optional[str] = None
    shipment_id: Optional[str] = None
    invoice_id: Optional[str] = None
    delay_reason: Optional[str] = None
    context_json: Dict[str, Any] = Field(default_factory=dict)
    updated_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PurchaseOrderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    rfq_id: str
    vendor_id: Optional[str] = None
    po_number: str
    status: str
    vendor_confirmation_status: str = "pending"
    vendor_confirmation_number: Optional[str] = None
    issued_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    confirmed_by_user_id: Optional[str] = None
    currency: str = "USD"
    subtotal: Optional[float] = None
    freight: Optional[float] = None
    taxes: Optional[float] = None
    total_amount: Optional[float] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    shipments: List[Dict[str, Any]] = Field(default_factory=list)
    goods_receipts: List[Dict[str, Any]] = Field(default_factory=list)
    invoices: List[Dict[str, Any]] = Field(default_factory=list)


class ShipmentEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    shipment_id: str
    event_type: str
    event_status: str
    location: Optional[str] = None
    message: Optional[str] = None
    occurred_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CarrierMilestoneSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    shipment_id: str
    milestone_code: str
    milestone_name: str
    milestone_status: str = "pending"
    description: Optional[str] = None
    location: Optional[str] = None
    estimated_at: Optional[datetime] = None
    actual_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CustomsEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    shipment_id: str
    country: Optional[str] = None
    status: str = "pending"
    message: Optional[str] = None
    held_reason: Optional[str] = None
    released_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GoodsReceiptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    purchase_order_id: str
    shipment_id: Optional[str] = None
    receipt_number: str
    receipt_status: str = "pending"
    received_quantity: Optional[float] = None
    confirmed_at: Optional[datetime] = None
    confirmed_by_user_id: Optional[str] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvoiceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    purchase_order_id: str
    vendor_id: Optional[str] = None
    invoice_number: str
    invoice_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    invoice_status: str = "issued"
    currency: str = "USD"
    subtotal: Optional[float] = None
    taxes: Optional[float] = None
    total_amount: Optional[float] = None
    matched_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    payment_state: Optional[Dict[str, Any]] = None


class PaymentStateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_id: str
    purchase_order_id: str
    status: str
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FulfillmentContextResponse(BaseModel):
    rfq_id: str
    project_id: Optional[str] = None
    rfq_status: Optional[str] = None
    execution_state: Optional[str] = None
    next_action: Optional[str] = None
    purchase_order: Optional[PurchaseOrderSchema] = None
    shipments: List[Dict[str, Any]] = Field(default_factory=list)
    shipment_events: List[ShipmentEventSchema] = Field(default_factory=list)
    carrier_milestones: List[CarrierMilestoneSchema] = Field(default_factory=list)
    customs_events: List[CustomsEventSchema] = Field(default_factory=list)
    goods_receipts: List[GoodsReceiptSchema] = Field(default_factory=list)
    invoices: List[InvoiceSchema] = Field(default_factory=list)
    payment_state: Optional[PaymentStateSchema] = None
    tracking_history: List[TrackingEntrySchema] = Field(default_factory=list)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    po_number: Optional[str] = None
    vendor_confirmation: Optional[str] = None
    tracking_number: Optional[str] = None
    carrier_name: Optional[str] = None
    eta: Optional[datetime] = None
    delay_reason: Optional[str] = None
    receipt_confirmation: Optional[str] = None


class PurchaseOrderCreateRequest(BaseModel):
    vendor_id: Optional[str] = None
    po_number: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    freight: Optional[float] = None
    taxes: Optional[float] = None
    total_amount: Optional[float] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PurchaseOrderConfirmRequest(BaseModel):
    vendor_confirmation_number: Optional[str] = None
    notes: Optional[str] = None


class ShipmentCreateRequest(BaseModel):
    carrier_name: Optional[str] = None
    carrier_code: Optional[str] = None
    tracking_number: Optional[str] = None
    status: Optional[str] = "shipped"
    eta: Optional[datetime] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    delay_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ShipmentEventCreateRequest(BaseModel):
    event_type: str
    event_status: Optional[str] = "recorded"
    location: Optional[str] = None
    message: Optional[str] = None
    occurred_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CarrierMilestoneCreateRequest(BaseModel):
    milestone_code: str
    milestone_name: str
    milestone_status: Optional[str] = "pending"
    description: Optional[str] = None
    location: Optional[str] = None
    estimated_at: Optional[datetime] = None
    actual_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CustomsEventCreateRequest(BaseModel):
    country: Optional[str] = None
    status: Optional[str] = "pending"
    message: Optional[str] = None
    held_reason: Optional[str] = None
    released_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GoodsReceiptCreateRequest(BaseModel):
    receipt_number: Optional[str] = None
    receipt_status: Optional[str] = "pending"
    received_quantity: Optional[float] = None
    confirmed_at: Optional[datetime] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvoiceCreateRequest(BaseModel):
    vendor_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    invoice_status: Optional[str] = "issued"
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    taxes: Optional[float] = None
    total_amount: Optional[float] = None
    matched_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaymentStateUpdateRequest(BaseModel):
    status: str
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)