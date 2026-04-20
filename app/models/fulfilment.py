"""
Shipment / GR / Invoice / Payment entities — physical + financial
fulfilment of a PO.

Contract anchors
----------------
§2.47 Shipment             §2.48 Shipment_Event (APPEND-ONLY)
§2.49 Goods_Receipt        §2.50 GR_Line
§2.51 Invoice              §2.52 Invoice_Line
§2.53 Payment              §2.54 Payment_Event (APPEND-ONLY)

State vocabularies
------------------
§3.8  SM-007 Shipment.state    §3.9  SM-008 Invoice.state
§3.32 Shipment_Event.source    §3.43 Payment.payment_method
§3.44 GR_Line.condition        §3.45 Invoice_Line.match_status
§3.59 Carrier (CN-10)          §3.85 Payment_Event.source

Conflict notes
--------------
* ``shipment.milestone_history_json`` is an APPEND-ONLY JSONB mirror of
  ``shipment_event`` rows (justified in §2.93).
* ``goods_receipt.attachments_json`` is a denormalized cache only;
  authoritative data = ``document`` table (see ``chat.py``).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    currency_code,
    enum_check,
    jsonb_array,
    jsonb_object,
    money,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    Carrier,
    GRLineCondition,
    InvoiceLineMatchStatus,
    InvoiceState,
    PaymentEventSource,
    PaymentMethod,
    ShipmentEventSource,
    ShipmentState,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shipment (§2.47)
# ─────────────────────────────────────────────────────────────────────────────


class Shipment(Base, CreatedAtMixin):
    """Outbound carrier shipment tied to a PO. SM-007 9-state machine."""

    __tablename__ = "shipment"

    shipment_id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = uuid_fk(
        "purchase_order.po_id", ondelete="CASCADE"
    )
    carrier: Mapped[str] = mapped_column(String(16), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(128), nullable=False)
    origin_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_location: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'BOOKED'")
    )
    current_milestone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # APPEND-ONLY mirror of shipment_event rows (justified in §2.93).
    milestone_history_json: Mapped[list] = jsonb_array()
    eta: Mapped[datetime | None] = tstz(nullable=True)
    last_carrier_update_at: Mapped[datetime | None] = tstz(nullable=True)
    delay_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    delay_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        enum_check("carrier", values_of(Carrier)),
        enum_check("state", values_of(ShipmentState)),
        Index("ix_shipment_po_id", "po_id"),
        Index("ix_shipment_state", "state"),
        Index("ix_shipment_last_carrier_update_at", "last_carrier_update_at"),
        Index("ix_shipment_tracking_number", "tracking_number"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ShipmentEvent (§2.48) — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class ShipmentEvent(Base, CreatedAtMixin):
    """Append-only carrier milestone event (webhook or polling)."""

    __tablename__ = "shipment_event"

    event_id: Mapped[uuid.UUID] = uuid_pk()
    shipment_id: Mapped[uuid.UUID] = uuid_fk(
        "shipment.shipment_id", ondelete="CASCADE"
    )
    milestone: Mapped[str] = mapped_column(String(64), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = tstz()
    received_at: Mapped[datetime] = tstz(default_now=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_payload_json: Mapped[dict] = jsonb_object()
    carrier_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        enum_check("source", values_of(ShipmentEventSource)),
        Index(
            "uq_shipment_event_carrier_event_id",
            "carrier_event_id",
            unique=True,
            postgresql_where=text("carrier_event_id IS NOT NULL"),
        ),
        Index(
            "ix_shipment_event_shipment_id_occurred_at",
            "shipment_id",
            "occurred_at",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GoodsReceipt (§2.49)
# ─────────────────────────────────────────────────────────────────────────────


class GoodsReceipt(Base, CreatedAtMixin):
    """Buyer confirmation that PO goods were received. ``attachments_json``
    is a denormalized cache — authoritative documents live in ``document``."""

    __tablename__ = "goods_receipt"

    gr_id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = uuid_fk(
        "purchase_order.po_id", ondelete="RESTRICT"
    )
    received_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    received_at: Mapped[datetime] = tstz(default_now=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FLAGGED: denormalized cache only. Authoritative data = document table.
    attachments_json: Mapped[list] = jsonb_array()

    __table_args__ = (
        Index("ix_goods_receipt_po_id", "po_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GRLine (§2.50)
# ─────────────────────────────────────────────────────────────────────────────


class GRLine(Base, CreatedAtMixin):
    """Per-PO-line received quantity + condition."""

    __tablename__ = "gr_line"

    gr_line_id: Mapped[uuid.UUID] = uuid_pk()
    gr_id: Mapped[uuid.UUID] = uuid_fk(
        "goods_receipt.gr_id", ondelete="CASCADE"
    )
    po_line_id: Mapped[uuid.UUID] = uuid_fk(
        "po_line.po_line_id", ondelete="RESTRICT"
    )
    quantity_received: Mapped[Decimal] = money()
    condition: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'OK'")
    )
    ncr_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        enum_check("condition", values_of(GRLineCondition)),
        CheckConstraint("quantity_received >= 0", name="quantity_received_nonneg"),
        Index("ix_gr_line_gr_id", "gr_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invoice (§2.51)
# ─────────────────────────────────────────────────────────────────────────────


class Invoice(Base, CreatedAtMixin):
    """Vendor-issued invoice against a PO. SM-008 11-state machine."""

    __tablename__ = "invoice"

    invoice_id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = uuid_fk(
        "purchase_order.po_id", ondelete="RESTRICT"
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal] = money()
    currency: Mapped[str] = currency_code()
    tax_amount: Mapped[Decimal] = money_default_zero()
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'RECEIVED'")
    )
    three_way_match_result_json: Mapped[dict] = jsonb_object()
    dispute_id: Mapped[uuid.UUID | None] = uuid_fk(
        "dispute.dispute_id", ondelete="SET NULL", nullable=True
    )
    received_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("state", values_of(InvoiceState)),
        UniqueConstraint(
            "vendor_id",
            "invoice_number",
            name="uq_invoice_vendor_id_invoice_number",
        ),
        Index("ix_invoice_po_id", "po_id"),
        Index("ix_invoice_state", "state"),
        Index("ix_invoice_due_date_state", "due_date", "state"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# InvoiceLine (§2.52)
# ─────────────────────────────────────────────────────────────────────────────


class InvoiceLine(Base, CreatedAtMixin):
    """Per-PO-line invoiced quantity / price + 3-way match status."""

    __tablename__ = "invoice_line"

    invoice_line_id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = uuid_fk(
        "invoice.invoice_id", ondelete="CASCADE"
    )
    po_line_id: Mapped[uuid.UUID] = uuid_fk(
        "po_line.po_line_id", ondelete="RESTRICT"
    )
    quantity_invoiced: Mapped[Decimal] = money()
    unit_price_invoiced: Mapped[Decimal] = money()
    line_total: Mapped[Decimal] = money()
    match_status: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        enum_check("match_status", values_of(InvoiceLineMatchStatus)),
        Index("ix_invoice_line_invoice_id", "invoice_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Payment (§2.53)
# ─────────────────────────────────────────────────────────────────────────────


class Payment(Base, CreatedAtMixin):
    """Outbound payment against an invoice. Status lifecycle is tracked in
    ``payment_event`` rows; the ``*_at`` columns here record key milestones."""

    __tablename__ = "payment"

    payment_id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = uuid_fk(
        "invoice.invoice_id", ondelete="RESTRICT"
    )
    amount: Mapped[Decimal] = money()
    currency: Mapped[str] = currency_code()
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_at: Mapped[datetime | None] = tstz(nullable=True)
    initiated_at: Mapped[datetime | None] = tstz(nullable=True)
    settled_at: Mapped[datetime | None] = tstz(nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        enum_check("payment_method", values_of(PaymentMethod)),
        # NULL reference numbers are intentionally allowed more than once per
        # invoice for partial-payment workflows. Non-NULL references are unique
        # per invoice to prevent bank confirmation double-posting.
        UniqueConstraint(
            "invoice_id",
            "reference_number",
            name="uq_payment_invoice_id_reference_number",
        ),
        Index("ix_payment_invoice_id", "invoice_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PaymentEvent (§2.54) — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class PaymentEvent(Base, CreatedAtMixin):
    """Append-only payment-status event (ERP, manual, or gateway webhook)."""

    __tablename__ = "payment_event"

    event_id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID] = uuid_fk(
        "payment.payment_id", ondelete="CASCADE"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = tstz()
    metadata_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        enum_check("source", values_of(PaymentEventSource)),
        Index("ix_payment_event_payment_id", "payment_id"),
        Index("ix_payment_event_payment_id_occurred_at", "payment_id", "occurred_at"),
    )


__all__ = [
    "Shipment",
    "ShipmentEvent",
    "GoodsReceipt",
    "GRLine",
    "Invoice",
    "InvoiceLine",
    "Payment",
    "PaymentEvent",
]
