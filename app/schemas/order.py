"""
order.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Order Execution Schema Layer

CONTRACT AUTHORITY: contract.md §2.44–2.56 (PurchaseOrder, POLine,
ChangeOrder, Shipment, ShipmentEvent, GoodsReceipt, GRLine, ApprovalRequest,
ApprovalDecision), §3.7 (SM-006: 15 PO states), §3.8 (SM-007: 9 Shipment
states), §3.14 (SM-011: ApprovalRequest), §4.7 (Orders endpoints), CN-10,
CN-12.

Key invariants:
  • CN-12: 15 PO states (adds CANCELLED, ON_HOLD, CHANGE_ORDER_PENDING to 12).
  • forex_rate_id_locked on PO is NOT NULL — locked at PO creation time.
  • po_number is UNIQUE human-readable sequential.
  • GR cannot be confirmed before PO reaches DELIVERED (SM-006 guard).
  • SLA monitoring fires at every state transition (SLA thresholds in SM-006).
  • Shipment.milestone_history_json is APPEND-ONLY (SM-007 invariant).
  • CN-10: Shipment.carrier uses 'other' (not 'custom').
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    DataFreshnessEnvelope,
    CurrencyCode,
    ApprovalDecisionValue,
    ApprovalRequestEntityType,
    ApprovalRequestStatus,
    Carrier,
    ChangeOrderStatus,
    GRLineCondition,
    IdempotencyKey,
    Money,
    PGIBase,
    PositiveMoney,
    PurchaseOrderStatus,
    ShipmentEventSource,
    ShipmentState,
    VendorOrderUpdateType,
)


# ──────────────────────────────────────────────────────────────────────────
# Purchase_Order (contract §2.44)
# ──────────────────────────────────────────────────────────────────────────

class PurchaseOrderResponse(PGIBase):
    """Full Purchase Order entity (SM-006, 15 states).

    forex_rate_id_locked: NOT NULL — locked at PO creation (SM-014).
    po_number: human-readable sequential (UNIQUE).
    """

    po_id: UUID
    po_number: str = Field(max_length=64)
    quote_id: UUID
    vendor_id: UUID
    project_id: UUID
    status: PurchaseOrderStatus
    total_value: PositiveMoney
    currency: CurrencyCode
    incoterms: Optional[str] = Field(default=None, max_length=16)
    payment_terms: Optional[str] = Field(default=None, max_length=64)
    expected_delivery_date: date = Field(description="ISO date YYYY-MM-DD.")
    actual_delivery_date: Optional[date] = None
    logistics_provider: Optional[str] = Field(default=None, max_length=64)
    tracking_number: Optional[str] = Field(default=None, max_length=128)
    forex_rate_id_locked: UUID = Field(
        description="FK forex_rate — locked at PO creation. NOT NULL."
    )
    approval_request_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None

    # Expanded sub-resources (GET /api/v1/orders/{id})
    lines: Optional[list["POLineSchema"]] = None
    shipments: Optional[list["ShipmentResponse"]] = None
    approvals: Optional[list["ApprovalRequestSchema"]] = None
    timeline: Optional[list[dict[str, Any]]] = None
    data_freshness: Optional[DataFreshnessEnvelope] = Field(
        default=None,
        description="Freshness of forex rate locked at PO creation.",
    )


class PurchaseOrderSummaryResponse(PGIBase):
    """Compact PO for list views."""

    po_id: UUID
    po_number: str
    vendor_id: UUID
    vendor_name: Optional[str] = None
    project_id: UUID
    status: PurchaseOrderStatus
    total_value: PositiveMoney
    currency: CurrencyCode
    expected_delivery_date: date
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/orders — create PO(s) from accepted quote(s)
# ──────────────────────────────────────────────────────────────────────────

class OrderCreateRequest(PGIBase):
    """Create one or more POs from accepted quotes.

    Multiple accepted_quote_ids supports split-BOM: one PO per vendor.
    Idempotency-Key header required.
    """

    accepted_quote_ids: list[UUID] = Field(
        min_length=1,
        description="One quote ID per vendor; creates one PO per vendor.",
    )
    approval_note: Optional[str] = Field(default=None, max_length=2000)


class OrderCreatePOResult(PGIBase):
    """Result for a single PO created within an order request."""

    po_id: UUID
    status: PurchaseOrderStatus
    approval_required: bool
    approval_request_id: Optional[UUID] = None


class OrderCreateResponse(PGIBase):
    """Response for POST /api/v1/orders."""

    purchase_orders: list[OrderCreatePOResult]
    data_freshness: Optional[DataFreshnessEnvelope] = None


# ──────────────────────────────────────────────────────────────────────────
# PO_Line (contract §2.45)
# ──────────────────────────────────────────────────────────────────────────

class POLineSchema(PGIBase):
    """A single line item within a Purchase Order.

    Traces back to: bom_line (requirement origin) and quote_line (pricing source).
    tolerance_qty_pct:   default 5 — threshold for three-way match.
    tolerance_price_pct: default 2 — threshold for three-way match.
    """

    po_line_id: UUID
    po_id: UUID
    quote_line_id: UUID
    bom_line_id: UUID
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price: PositiveMoney
    line_total: PositiveMoney
    tolerance_qty_pct: Decimal = Field(default=Decimal("5"), decimal_places=2)
    tolerance_price_pct: Decimal = Field(default=Decimal("2"), decimal_places=2)

    # Denormalized
    normalized_name: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Change_Order (contract §2.46)
# ──────────────────────────────────────────────────────────────────────────

class ChangeOrderSchema(PGIBase):
    """A proposed change to an existing Purchase Order.

    Creating a ChangeOrder transitions PO → CHANGE_ORDER_PENDING (SM-006).
    On approval, PO reverts to its prior state with updated terms.
    On rejection, PO reverts with no changes.
    """

    change_order_id: UUID
    po_id: UUID
    requested_by: UUID
    reason: str
    diff_json: dict[str, Any] = Field(
        description="JSON diff of proposed changes vs current PO terms."
    )
    status: ChangeOrderStatus
    created_at: datetime


class ChangeOrderCreateRequest(PGIBase):
    """Create a change order for an existing PO."""

    reason: str = Field(min_length=1, max_length=2000)
    diff_json: dict[str, Any] = Field(
        description="Proposed changes to PO terms.",
        min_length=1,
    )


# ──────────────────────────────────────────────────────────────────────────
# Shipment (contract §2.47)
# ──────────────────────────────────────────────────────────────────────────

class ShipmentResponse(PGIBase):
    """Shipment entity with carrier tracking (SM-007).

    milestone_history_json: APPEND-ONLY mirror of shipment_event rows.
    CN-10: carrier uses 'other' for unlisted carriers.
    delay_flag: set True when current ETA exceeds original transit estimate.
    """

    shipment_id: UUID
    po_id: UUID
    carrier: Carrier
    tracking_number: str = Field(max_length=128)
    origin_location: Optional[str] = Field(default=None, max_length=255)
    destination_location: Optional[str] = Field(default=None, max_length=255)
    state: ShipmentState
    current_milestone: Optional[str] = Field(default=None, max_length=64)
    milestone_history_json: list[Any] = Field(
        default_factory=list,
        description="APPEND-ONLY sequence of carrier milestone events.",
    )
    eta: Optional[datetime] = None
    last_carrier_update_at: Optional[datetime] = None
    delay_flag: bool = False
    delay_reason: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime

    events: Optional[list["ShipmentEventSchema"]] = None


# ──────────────────────────────────────────────────────────────────────────
# Shipment_Event (contract §2.48)
# ──────────────────────────────────────────────────────────────────────────

class ShipmentEventSchema(PGIBase):
    """An individual carrier milestone event (APPEND-ONLY).

    source: webhook (real-time) or polling (fallback).
    raw_payload_json: original carrier webhook payload preserved verbatim.
    """

    event_id: UUID
    shipment_id: UUID
    milestone: str = Field(max_length=64)
    location: Optional[str] = Field(default=None, max_length=255)
    occurred_at: datetime
    received_at: datetime
    source: ShipmentEventSource
    raw_payload_json: dict[str, Any] = Field(default_factory=dict)
    carrier_event_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Carrier-native event identifier for idempotency and tracing.",
    )


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/orders/{id}/book-logistics
# ──────────────────────────────────────────────────────────────────────────

class BookLogisticsRequest(PGIBase):
    """Buyer books integrated carrier shipment when vendor ships_on_their_own=False."""

    carrier: Carrier = Field(description="Must not be 'other' for booking.")
    service_level: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def carrier_must_be_bookable(self) -> "BookLogisticsRequest":
        if self.carrier == Carrier.OTHER:
            raise ValueError(
                "carrier 'other' cannot be used for integrated logistics booking. "
                "Select DHL, FedEx, UPS, or Maersk."
            )
        return self


class BookLogisticsResponse(PGIBase):
    """Booking confirmation with shipment ID and tracking number."""

    shipment_id: UUID
    tracking_number: str
    waybill_url: str


# ──────────────────────────────────────────────────────────────────────────
# Goods_Receipt (contract §2.49)
# ──────────────────────────────────────────────────────────────────────────

class GoodsReceiptSchema(PGIBase):
    """Record of a buyer confirming receipt of goods against a PO.

    attachments_json: denormalized cache; authoritative data in document table.
    """

    gr_id: UUID
    po_id: UUID
    received_by: UUID
    received_at: datetime
    notes: Optional[str] = None
    attachments_json: list[Any] = Field(
        default_factory=list,
        description="Denormalized attachment cache. Authoritative rows in document table.",
    )
    lines: Optional[list["GRLineSchema"]] = None


# ──────────────────────────────────────────────────────────────────────────
# GR_Line (contract §2.50)
# ──────────────────────────────────────────────────────────────────────────

class GRLineSchema(PGIBase):
    """A single line within a goods receipt — quantity and condition per PO line."""

    gr_line_id: UUID
    gr_id: UUID
    po_line_id: UUID
    quantity_received: Decimal = Field(ge=Decimal("0"))
    condition: GRLineCondition
    ncr_notes: Optional[str] = Field(
        default=None, description="Required when condition = 'NCR'."
    )

    @model_validator(mode="after")
    def ncr_notes_required(self) -> "GRLineSchema":
        if self.condition == GRLineCondition.NCR and not self.ncr_notes:
            raise ValueError("ncr_notes required when condition = 'NCR'.")
        return self


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/orders/{id}/confirm-gr
# ──────────────────────────────────────────────────────────────────────────

class GRLineConfirmRequest(PGIBase):
    """Per-line confirmation data within a GR confirmation request."""

    po_line_id: UUID
    quantity_received: Decimal = Field(ge=Decimal("0"))
    condition: GRLineCondition
    ncr_notes: Optional[str] = None


class ConfirmGRRequest(PGIBase):
    """Buyer confirms goods receipt — transitions PO DELIVERED → GR_CONFIRMED."""

    lines: list[GRLineConfirmRequest] = Field(min_length=1)
    notes: Optional[str] = Field(default=None, max_length=2000)
    attachment_ids: list[UUID] = Field(
        default_factory=list,
        description="Document vault UUIDs for GR-related attachments.",
    )


class ConfirmGRResponse(PGIBase):
    """Response after goods receipt confirmation."""

    gr_id: UUID
    po_status: PurchaseOrderStatus  # Always GR_CONFIRMED


# ──────────────────────────────────────────────────────────────────────────
# Vendor production updates — POST /api/v1/vendor/orders/{id}/updates
# ──────────────────────────────────────────────────────────────────────────

class VendorOrderUpdateRequest(PGIBase):
    """Vendor submits a production status update for an order.

    update_type drives the PO state machine transition:
      production_started → VENDOR_ACCEPTED → PRODUCTION_STARTED
      quality_check      → PRODUCTION_STARTED → QUALITY_CHECK
      packed             → QUALITY_CHECK → PACKED
      shipped            → PACKED → SHIPPED (with shipment creation)
      text_update        → no state change; logged as activity
    """

    update_type: VendorOrderUpdateType
    message: Optional[str] = Field(default=None, max_length=2000)
    photo_urls: list[str] = Field(default_factory=list)
    completion_pct: Optional[int] = Field(default=None, ge=0, le=100)


class VendorOrderUpdateResponse(PGIBase):
    """Response after vendor submits a production update."""

    update_id: UUID
    po_status: PurchaseOrderStatus


# ──────────────────────────────────────────────────────────────────────────
# Approval_Request (contract §2.55)
# ──────────────────────────────────────────────────────────────────────────

class ApprovalRequestSchema(PGIBase):
    """An approval request for a PO, invoice, or change order (SM-011).

    entity_type: 'purchase_order' | 'invoice' | 'change_order'.
    threshold_amount: the value that triggered the approval requirement.
    deadline: SLA for the approver to respond; EXPIRED if elapsed.
    """

    approval_id: UUID
    entity_type: ApprovalRequestEntityType
    entity_id: UUID
    requested_by: UUID
    assigned_to: UUID
    threshold_amount: Money
    status: ApprovalRequestStatus
    deadline: datetime
    decided_at: Optional[datetime] = None
    created_at: datetime

    decisions: Optional[list["ApprovalDecisionSchema"]] = None


class ApprovalDecisionSchema(PGIBase):
    """An append-only record of an approver's decision (SM-011)."""

    decision_id: UUID
    approval_id: UUID
    decision: ApprovalDecisionValue
    decided_by: UUID
    notes: Optional[str] = None
    decided_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/orders/{id}/approve
# ──────────────────────────────────────────────────────────────────────────

class OrderApproveRequest(PGIBase):
    """Approver decision on a PO approval request."""

    decision: ApprovalDecisionValue
    notes: Optional[str] = Field(default=None, max_length=2000)


class OrderApproveResponse(PGIBase):
    """Response after an approval decision is recorded."""

    approval_id: UUID
    decision: ApprovalDecisionValue


# Forward reference resolution
PurchaseOrderResponse.model_rebuild()
ShipmentResponse.model_rebuild()
GoodsReceiptSchema.model_rebuild()
ApprovalRequestSchema.model_rebuild()
