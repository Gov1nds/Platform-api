"""
RFQ / Quote / Award / PO / Change-Order entities — the buyer ↔ vendor
commercial transaction chain.

Contract anchors
----------------
§2.36 RFQ                      §2.37 RFQ_Line
§2.38 RFQ_Vendor_Invite        §2.39 Quote
§2.40 Quote_Line               §2.41 Quote_Revision (APPEND-ONLY)
§2.42 Award_Decision           §2.43 Comparison_Run
§2.44 Purchase_Order           §2.45 PO_Line
§2.46 Change_Order

State vocabularies
------------------
§3.5  SM-004 RFQ.status            §3.6 SM-005 Quote.status
§3.7  SM-006 Purchase_Order.status §3.25 Change_Order.status
§3.33 Quote.source_channel         §3.34 RFQ.send_channel
§3.35 RFQ_Vendor_Invite.delivery_method

Conflict notes
--------------
* CN-4: ``rfq.selected_vendors`` and ``rfq.selected_lines`` MUST NOT exist
  as array columns — ``rfq_line`` and ``rfq_vendor_invite`` are the
  authoritative child tables.
* CN-5/6/12/13: RFQ / Quote / PO vocabularies are the full uppercase sets
  from requirements.yaml, and minimum 3 vendors per RFQ line is enforced
  in the service layer before DRAFT → SENT.
* ``rfq.terms_snapshot`` is IMMUTABLE after SENT — app-enforced.
* ``quote.forex_rate_id_locked`` / ``quote.tariff_snapshot_id`` lock
  market-data rows to the quote at SUBMITTED time (CN-9, LOCKED state).
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
    Integer,
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
    TimestampMixin,
    currency_code,
    currency_code_nullable,
    enum_check,
    jsonb_array,
    jsonb_object,
    money,
    money_default_zero,
    money_nullable,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    ChangeOrderStatus,
    PurchaseOrderStatus,
    QuoteSourceChannel,
    QuoteStatus,
    RFQInviteDeliveryMethod,
    RFQSendChannel,
    RFQStatus,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# RFQ (§2.36)
# ─────────────────────────────────────────────────────────────────────────────


class RFQ(Base, TimestampMixin):
    """Request-for-quotation. Vendor selections live in ``rfq_vendor_invite``
    and line selections live in ``rfq_line`` (CN-4)."""

    __tablename__ = "rfq"

    rfq_id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="RESTRICT"
    )
    created_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    deadline: Mapped[datetime] = tstz()
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'DRAFT'")
    )
    vendor_selection_reason_json: Mapped[dict] = jsonb_object()
    # IMMUTABLE after SENT (app-enforced): captures terms at dispatch time.
    terms_snapshot: Mapped[dict] = jsonb_object()
    send_channel: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'multi'")
    )
    sent_at: Mapped[datetime | None] = tstz(nullable=True)
    closed_at: Mapped[datetime | None] = tstz(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        enum_check("status", values_of(RFQStatus)),
        enum_check("send_channel", values_of(RFQSendChannel)),
        CheckConstraint("deadline > created_at", name="deadline_after_created_at"),
        UniqueConstraint("idempotency_key", name="uq_rfq_idempotency_key"),
        Index("ix_rfq_project_id_status", "project_id", "status"),
        Index("ix_rfq_deadline", "deadline"),
        Index("ix_rfq_idempotency_key", "idempotency_key"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RFQLine (§2.37)
# ─────────────────────────────────────────────────────────────────────────────


class RFQLine(Base, CreatedAtMixin):
    """BOM_Line included in an RFQ."""

    __tablename__ = "rfq_line"

    rfq_line_id: Mapped[uuid.UUID] = uuid_pk()
    rfq_id: Mapped[uuid.UUID] = uuid_fk("rfq.rfq_id", ondelete="CASCADE")
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="RESTRICT"
    )
    quantity_requested: Mapped[Decimal] = money()
    required_by_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint("quantity_requested > 0", name="quantity_requested_positive"),
        UniqueConstraint(
            "rfq_id",
            "bom_line_id",
            name="uq_rfq_line_rfq_id_bom_line_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RFQVendorInvite (§2.38)
# ─────────────────────────────────────────────────────────────────────────────


class RFQVendorInvite(Base, CreatedAtMixin):
    """Vendor invited to respond to an RFQ."""

    __tablename__ = "rfq_vendor_invite"

    invite_id: Mapped[uuid.UUID] = uuid_pk()
    rfq_id: Mapped[uuid.UUID] = uuid_fk("rfq.rfq_id", ondelete="CASCADE")
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    delivery_method: Mapped[str] = mapped_column(String(16), nullable=False)
    unique_token: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime | None] = tstz(nullable=True)
    viewed_at: Mapped[datetime | None] = tstz(nullable=True)
    responded_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("delivery_method", values_of(RFQInviteDeliveryMethod)),
        UniqueConstraint(
            "rfq_id", "vendor_id", name="uq_rfq_vendor_invite_rfq_id_vendor_id"
        ),
        UniqueConstraint("unique_token", name="uq_rfq_vendor_invite_unique_token"),
        Index("ix_rfq_vendor_invite_unique_token", "unique_token"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Quote (§2.39)
# ─────────────────────────────────────────────────────────────────────────────


class Quote(Base, TimestampMixin):
    """Vendor-submitted quote against an RFQ.

    ``forex_rate_id_locked`` and ``tariff_snapshot_id`` lock the underlying
    market-data rows at SUBMITTED time (CN-9 LOCKED state).
    """

    __tablename__ = "quote"

    quote_id: Mapped[uuid.UUID] = uuid_pk()
    rfq_id: Mapped[uuid.UUID] = uuid_fk("rfq.rfq_id", ondelete="RESTRICT")
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'PENDING'")
    )
    quote_currency: Mapped[str] = currency_code()
    valid_until: Mapped[datetime] = tstz()
    quote_date: Mapped[datetime] = tstz()
    source_channel: Mapped[str] = mapped_column(String(8), nullable=False)
    total_quoted_value: Mapped[Decimal] = money_default_zero()
    forex_rate_at_submission: Mapped[Decimal | None] = money_nullable()
    forex_rate_id_locked: Mapped[uuid.UUID | None] = uuid_fk(
        "forex_rate.rate_id", ondelete="RESTRICT", nullable=True
    )
    tariff_snapshot_id: Mapped[uuid.UUID | None] = uuid_fk(
        "tariff_rate.tariff_id", ondelete="RESTRICT", nullable=True
    )
    tlc_calculated: Mapped[Decimal | None] = money_nullable()
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        enum_check("status", values_of(QuoteStatus)),
        enum_check("source_channel", values_of(QuoteSourceChannel)),
        CheckConstraint(
            "valid_until > quote_date",
            name="quote_valid_until_after_quote_date",
        ),
        UniqueConstraint(
            "rfq_id", "vendor_id", name="uq_quote_rfq_id_vendor_id"
        ),
        Index("ix_quote_status", "status"),
        Index("ix_quote_valid_until", "valid_until"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# QuoteLine (§2.40)
# ─────────────────────────────────────────────────────────────────────────────


class QuoteLine(Base, CreatedAtMixin):
    """Per-line vendor pricing on a Quote."""

    __tablename__ = "quote_line"

    quote_line_id: Mapped[uuid.UUID] = uuid_pk()
    quote_id: Mapped[uuid.UUID] = uuid_fk(
        "quote.quote_id", ondelete="CASCADE"
    )
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="RESTRICT"
    )
    unit_price: Mapped[Decimal] = money()
    tooling_cost: Mapped[Decimal] = money_default_zero()
    moq: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default=text("1")
    )
    lead_time_weeks: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    payment_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_docs_attached: Mapped[list] = jsonb_array()
    tax_tariff_impact: Mapped[Decimal] = money_default_zero()
    freight_estimate: Mapped[Decimal] = money_default_zero()
    tlc_unit: Mapped[Decimal] = money()
    tlc_total: Mapped[Decimal] = money()
    substitute_proposed: Mapped[uuid.UUID | None] = uuid_fk(
        "part_master.part_id", ondelete="SET NULL", nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
        CheckConstraint("tooling_cost >= 0", name="tooling_cost_nonneg"),
        CheckConstraint("moq > 0", name="moq_positive"),
        CheckConstraint("lead_time_weeks > 0", name="lead_time_weeks_positive"),
        UniqueConstraint(
            "quote_id",
            "bom_line_id",
            name="uq_quote_line_quote_id_bom_line_id",
        ),
        Index("ix_quote_line_bom_line_id", "bom_line_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# QuoteRevision (§2.41) — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class QuoteRevision(Base, CreatedAtMixin):
    """Append-only revision log for a Quote."""

    __tablename__ = "quote_revision"

    revision_id: Mapped[uuid.UUID] = uuid_pk()
    quote_id: Mapped[uuid.UUID] = uuid_fk(
        "quote.quote_id", ondelete="CASCADE"
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    diff_json: Mapped[dict] = jsonb_object()
    submitted_at: Mapped[datetime] = tstz(default_now=True)
    offer_event_id: Mapped[uuid.UUID | None] = uuid_fk(
        "offer_event.offer_id", ondelete="SET NULL", nullable=True
    )

    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        UniqueConstraint(
            "quote_id",
            "revision_number",
            name="uq_quote_revision_quote_id_revision_number",
        ),
        Index("ix_quote_revision_offer_event_id", "offer_event_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AwardDecision (§2.42)
# ─────────────────────────────────────────────────────────────────────────────


class AwardDecision(Base, CreatedAtMixin):
    """Which vendor won which BOM_Line — result of a Comparison_Run."""

    __tablename__ = "award_decision"

    award_id: Mapped[uuid.UUID] = uuid_pk()
    rfq_id: Mapped[uuid.UUID] = uuid_fk("rfq.rfq_id", ondelete="RESTRICT")
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="RESTRICT"
    )
    quote_line_id: Mapped[uuid.UUID] = uuid_fk(
        "quote_line.quote_line_id", ondelete="RESTRICT"
    )
    awarded_vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    awarded_at: Mapped[datetime] = tstz(default_now=True)
    decided_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    comparison_run_id: Mapped[uuid.UUID] = uuid_fk(
        "comparison_run.run_id", ondelete="RESTRICT"
    )

    __table_args__ = (
        UniqueConstraint(
            "rfq_id",
            "bom_line_id",
            name="uq_award_decision_rfq_id_bom_line_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ComparisonRun (§2.43)
# ─────────────────────────────────────────────────────────────────────────────


class ComparisonRun(Base, CreatedAtMixin):
    """TLC-matrix snapshot used by quote-comparison UI. ``snapshot_json``
    captures the full matrix + locked rates (immutable post-creation)."""

    __tablename__ = "comparison_run"

    run_id: Mapped[uuid.UUID] = uuid_pk()
    rfq_id: Mapped[uuid.UUID] = uuid_fk("rfq.rfq_id", ondelete="RESTRICT")
    snapshot_json: Mapped[dict] = jsonb_object()
    auto_select_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index("ix_comparison_run_rfq_id", "rfq_id"),
        Index("ix_comparison_run_rfq_id_created_at", "rfq_id", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PurchaseOrder (§2.44)
# ─────────────────────────────────────────────────────────────────────────────


class PurchaseOrder(Base, TimestampMixin):
    """Human-numbered purchase order. SM-006 15-state vocabulary (CN-12)."""

    __tablename__ = "purchase_order"

    po_id: Mapped[uuid.UUID] = uuid_pk()
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_id: Mapped[uuid.UUID] = uuid_fk(
        "quote.quote_id", ondelete="RESTRICT"
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    project_id: Mapped[uuid.UUID] = uuid_fk(
        "project.project_id", ondelete="RESTRICT"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PO_APPROVED'")
    )
    total_value: Mapped[Decimal] = money()
    currency: Mapped[str] = currency_code()
    incoterms: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    logistics_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    forex_rate_id_locked: Mapped[uuid.UUID] = uuid_fk(
        "forex_rate.rate_id", ondelete="RESTRICT"
    )
    approval_request_id: Mapped[uuid.UUID | None] = uuid_fk(
        "approval_request.approval_id", ondelete="SET NULL", nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    closed_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("status", values_of(PurchaseOrderStatus)),
        # Intentional model invariant: persisted POs must have positive value.
        # API schemas that allow zero need to reject zero before persistence.
        CheckConstraint("total_value > 0", name="total_value_positive"),
        UniqueConstraint("po_number", name="uq_purchase_order_po_number"),
        UniqueConstraint(
            "idempotency_key", name="uq_purchase_order_idempotency_key"
        ),
        Index(
            "ix_purchase_order_project_id_status", "project_id", "status"
        ),
        Index("ix_purchase_order_vendor_id_status", "vendor_id", "status"),
        Index("ix_purchase_order_status", "status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# POLine (§2.45)
# ─────────────────────────────────────────────────────────────────────────────


class POLine(Base, CreatedAtMixin):
    """PO line item with buyer-side qty / price tolerances for 3-way match."""

    __tablename__ = "po_line"

    po_line_id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = uuid_fk(
        "purchase_order.po_id", ondelete="CASCADE"
    )
    quote_line_id: Mapped[uuid.UUID] = uuid_fk(
        "quote_line.quote_line_id", ondelete="RESTRICT"
    )
    bom_line_id: Mapped[uuid.UUID] = uuid_fk(
        "bom_line.bom_line_id", ondelete="RESTRICT"
    )
    quantity: Mapped[Decimal] = money()
    unit_price: Mapped[Decimal] = money()
    line_total: Mapped[Decimal] = money()
    tolerance_qty_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("5")
    )
    tolerance_price_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("2")
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
        UniqueConstraint("po_id", "quote_line_id", name="uq_po_line_po_id_quote_line_id"),
        Index("ix_po_line_po_id", "po_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ChangeOrder (§2.46)
# ─────────────────────────────────────────────────────────────────────────────


class ChangeOrder(Base, CreatedAtMixin):
    """Proposed modification to a live PO — drives
    ``PurchaseOrder.status = CHANGE_ORDER_PENDING`` until approved/rejected."""

    __tablename__ = "change_order"

    change_order_id: Mapped[uuid.UUID] = uuid_pk()
    po_id: Mapped[uuid.UUID] = uuid_fk(
        "purchase_order.po_id", ondelete="CASCADE"
    )
    requested_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[dict] = jsonb_object()
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )

    __table_args__ = (
        enum_check("status", values_of(ChangeOrderStatus)),
        Index("ix_change_order_po_id", "po_id"),
        Index("ix_change_order_status", "status"),
    )


__all__ = [
    "RFQ",
    "RFQLine",
    "RFQVendorInvite",
    "Quote",
    "QuoteLine",
    "QuoteRevision",
    "AwardDecision",
    "ComparisonRun",
    "PurchaseOrder",
    "POLine",
    "ChangeOrder",
]
