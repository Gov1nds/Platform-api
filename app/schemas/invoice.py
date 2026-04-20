"""
invoice.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Invoice, Payment & Financial Exception Schema Layer

CONTRACT AUTHORITY: contract.md §2.51–2.58 (Invoice, InvoiceLine, Payment,
PaymentEvent, ApprovalRequest [shared with order.py], Dispute, ExceptionCase),
§3.9 (SM-008: Invoice.state 11 states), requirements.yaml three_way_invoice_match.

Invariants:
  • UNIQUE constraint on invoice: (vendor_id, invoice_number) — duplicate rejection.
  • Tolerance defaults: qty ±5%, price ±2% — configurable per org.
  • Three-way match: Invoice_Line vs PO_Line vs GR_Line within tolerance.
  • VALIDATION_FAILED or mismatched lines → create Dispute automatically.
  • Dispute.entity_type: 'invoice' | 'po' | 'shipment'.
  • Exception_Case is for operational exceptions (SLA breach, OCR failure, etc.).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import (
    CurrencyCode,
    DisputeEntityType,
    DisputeStatus,
    ExceptionCaseStatus,
    ExceptionType,
    InvoiceLineMatchStatus,
    InvoiceState,
    Money,
    PGIBase,
    PaymentMethod,
    PaymentEventSource,
    PositiveMoney,
    Severity,
    SignedMoney,
    TIMESTAMPTZ,
)


# ──────────────────────────────────────────────────────────────────────────
# Invoice (contract §2.51)
# ──────────────────────────────────────────────────────────────────────────

class InvoiceSchema(PGIBase):
    """Full Invoice entity (SM-008).

    UNIQUE: (vendor_id, invoice_number) — duplicate invoice_number per vendor rejected.
    three_way_match_result_json: populated after VALIDATING completes.
    dispute_id: set when state = DISPUTED.
    """

    invoice_id: UUID
    po_id: UUID
    vendor_id: UUID
    invoice_number: str = Field(max_length=64)
    invoice_date: date = Field(description="ISO date YYYY-MM-DD.")
    due_date: date = Field(description="ISO date YYYY-MM-DD.")
    total_amount: SignedMoney
    currency: CurrencyCode
    tax_amount: SignedMoney = Field(default=Decimal("0"))
    state: InvoiceState
    three_way_match_result_json: dict[str, Any] = Field(default_factory=dict)
    dispute_id: Optional[UUID] = None
    received_at: datetime

    lines: Optional[list["InvoiceLineSchema"]] = None


class InvoiceSummarySchema(PGIBase):
    """Compact invoice for list views."""

    invoice_id: UUID
    po_id: UUID
    vendor_id: UUID
    vendor_name: Optional[str] = None
    invoice_number: str
    invoice_date: date
    due_date: date
    total_amount: SignedMoney
    currency: CurrencyCode
    state: InvoiceState
    received_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Invoice_Line (contract §2.52)
# ──────────────────────────────────────────────────────────────────────────

class InvoiceLineSchema(PGIBase):
    """A single line in a vendor invoice matched against a PO line.

    match_status: result of three-way match for this line.
    MATCHED:                  within tolerance on both qty and price.
    QTY_OUT_OF_TOLERANCE:     quantity deviation exceeds po_line.tolerance_qty_pct.
    PRICE_OUT_OF_TOLERANCE:   price deviation exceeds po_line.tolerance_price_pct.
    NO_MATCH:                 no corresponding PO line found.
    """

    invoice_line_id: UUID
    invoice_id: UUID
    po_line_id: UUID
    quantity_invoiced: Decimal
    unit_price_invoiced: PositiveMoney
    line_total: SignedMoney
    match_status: InvoiceLineMatchStatus


# ──────────────────────────────────────────────────────────────────────────
# Payment (contract §2.53)
# ──────────────────────────────────────────────────────────────────────────

class PaymentSchema(PGIBase):
    """A payment record against an approved invoice.

    v1 does NOT process payments directly — informational tracking only
    (requirements.yaml assumptions/payment_rails).
    """

    payment_id: UUID
    invoice_id: UUID
    amount: Money
    currency: CurrencyCode
    payment_method: PaymentMethod
    scheduled_at: Optional[TIMESTAMPTZ] = None
    initiated_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    reference_number: Optional[str] = Field(default=None, max_length=128)

    events: Optional[list["PaymentEventSchema"]] = None


class PaymentCreateRequest(PGIBase):
    """Record a payment initiation against an invoice."""

    invoice_id: UUID
    amount: Money
    currency: CurrencyCode
    payment_method: PaymentMethod
    scheduled_at: Optional[TIMESTAMPTZ] = None
    reference_number: Optional[str] = Field(default=None, max_length=128)


# ──────────────────────────────────────────────────────────────────────────
# Payment_Event (contract §2.54)
# ──────────────────────────────────────────────────────────────────────────

class PaymentEventSchema(PGIBase):
    """APPEND-ONLY log of payment status transitions.

    source: erp | manual | gateway_webhook.
    Covers: payment initiated, bank confirmation, settlement, rejection.
    """

    event_id: UUID
    payment_id: UUID
    status: str = Field(max_length=32)
    source: PaymentEventSource
    occurred_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Dispute (contract §2.57)
# ──────────────────────────────────────────────────────────────────────────

class DisputeSchema(PGIBase):
    """A formal dispute raised against an invoice, PO, or shipment.

    Auto-created by Repo C when three-way match fails outside tolerance.
    May also be raised manually by buyer or vendor.
    entity_type: 'invoice' | 'po' | 'shipment'.
    """

    dispute_id: UUID
    entity_type: DisputeEntityType
    entity_id: UUID
    raised_by: UUID
    reason: str
    status: DisputeStatus
    resolution_notes: Optional[str] = None
    opened_at: datetime
    resolved_at: Optional[datetime] = None


class DisputeCreateRequest(PGIBase):
    """Manually raise a dispute against an entity."""

    entity_type: DisputeEntityType
    entity_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class DisputeResolveRequest(PGIBase):
    """Resolve an open dispute."""

    resolution_notes: str = Field(min_length=1, max_length=2000)


# ──────────────────────────────────────────────────────────────────────────
# Exception_Case (contract §2.58)
# ──────────────────────────────────────────────────────────────────────────

class ExceptionCaseSchema(PGIBase):
    """An operational exception requiring human intervention.

    Created by Repo C's SLA monitor, OCR pipeline, or three-way match worker.
    exception_type categories:
      sla_breach:         vendor silent past SLA threshold.
      stale_tracking:     no Shipment_Event for 12h.
      low_confidence_ocr: OCR confidence below threshold on quote document.
      three_way_mismatch: three-way match failed and Dispute created.
      other:              catch-all for ad hoc exceptions.
    """

    case_id: UUID
    entity_type: str = Field(max_length=32)
    entity_id: UUID
    exception_type: ExceptionType
    severity: Severity
    assigned_to: Optional[UUID] = None
    status: ExceptionCaseStatus
    created_at: datetime


class ExceptionCaseUpdateRequest(PGIBase):
    """Update an exception case (assign, escalate, resolve)."""

    assigned_to: Optional[UUID] = None
    status: Optional[ExceptionCaseStatus] = None
    severity: Optional[Severity] = None


# Forward reference resolution
InvoiceSchema.model_rebuild()
PaymentSchema.model_rebuild()
