"""
RFQ, Quote, PO, Invoice, Payment, Goods Receipt, and Approval models.

References: GAP-009 (SM-004/SM-005), GAP-010 (SM-006), GAP-012 (SM-008),
            GAP-018, GAP-029, state-machines.md FSD-04/05/06/08,
            canonical-domain-model.md BC-09/10/11/12/13
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Integer, DateTime, ForeignKey, Numeric,
    Boolean, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# ── RFQ (SM-004) ────────────────────────────────────────────────────────────

class RFQBatch(Base):
    """
    RFQ entity. Status follows RFQStatus enum (SM-004):
    DRAFT → SENT → PARTIALLY_RESPONDED → FULLY_RESPONDED → CLOSED | EXPIRED | CANCELLED
    """
    __tablename__ = "rfq_batches"
    __table_args__ = (
        Index("ix_rfq_project", "project_id"),
        Index("ix_rfq_org", "organization_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    guest_session_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(40), nullable=False, default="DRAFT")  # RFQStatus (SM-004)
    notes = Column(Text, nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)
    minimum_vendors = Column(Integer, nullable=False, default=3)  # SM-004 guard
    terms_snapshot_json = Column(JSONB, nullable=False, default=dict)  # locked at SENT (INV-03)
    batch_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")
    invitations = relationship("RFQVendorInvitation", back_populates="rfq", cascade="all, delete-orphan")
    quote_headers = relationship("RFQQuoteHeader", back_populates="rfq", cascade="all, delete-orphan")


class RFQItem(Base):
    __tablename__ = "rfq_items"
    __table_args__ = (
        Index("ix_rfq_items_rfq", "rfq_batch_id"),
        Index("ix_rfq_items_bom_line", "bom_line_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # bom_line_id is the canonical lineage FK (DG-006, INV-06)
    bom_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=True,
    )
    part_key = Column(Text, nullable=True)
    requested_quantity = Column(Numeric(20, 8), nullable=False, default=1)
    requested_material = Column(Text, nullable=True)
    requested_process = Column(Text, nullable=True)
    drawing_required = Column(Boolean, nullable=False, default=False)
    spec_summary = Column(JSONB, nullable=False, default=dict)
    status = Column(Text, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    rfq = relationship("RFQBatch", back_populates="items")


class RFQVendorInvitation(Base):
    __tablename__ = "rfq_vendor_invitations"
    __table_args__ = (
        Index("ix_rvi_rfq", "rfq_batch_id"),
        Index("ix_rvi_vendor", "vendor_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(40), nullable=False, default="PENDING")  # InvitationStatus
    invited_at = Column(DateTime(timezone=True), default=_now)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    portal_token = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    rfq = relationship("RFQBatch", back_populates="invitations")
    vendor = relationship("Vendor")
    status_history = relationship(
        "InvitationStatusEvent", back_populates="invitation", cascade="all, delete-orphan"
    )


class InvitationStatusEvent(Base):
    __tablename__ = "invitation_status_events"
    __table_args__ = {"schema": "sourcing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    invitation_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_vendor_invitations.id", ondelete="CASCADE"),
        nullable=False,
    )
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    invitation = relationship("RFQVendorInvitation", back_populates="status_history")


# ── Quote (SM-005) ──────────────────────────────────────────────────────────

class RFQQuoteHeader(Base):
    """
    Quote entity. Status follows QuoteStatus enum (SM-005):
    PENDING → SUBMITTED → REVISION_REQUESTED → REVISED → ACCEPTED | REJECTED | EXPIRED | WITHDRAWN
    """
    __tablename__ = "rfq_quote_headers"
    __table_args__ = (
        Index("ix_rqh_rfq", "rfq_batch_id"),
        Index("ix_rqh_vendor", "vendor_id"),
        Index("ix_rqh_org", "organization_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    quote_number = Column(Text, nullable=True)
    quote_status = Column(String(40), nullable=False, default="PENDING")  # QuoteStatus (SM-005)
    award_status = Column(String(40), nullable=False, default="PENDING")  # PENDING, ACCEPTED, REJECTED
    quote_currency = Column(String(3), nullable=False, default="USD")
    incoterms = Column(Text, nullable=True)
    quote_version = Column(Integer, nullable=False, default=1)
    is_revision = Column(Boolean, nullable=False, default=False)
    parent_quote_id = Column(UUID(as_uuid=False), nullable=True)

    # Monetary — Numeric(20,8)
    subtotal = Column(Numeric(20, 8), nullable=True)
    freight = Column(Numeric(20, 8), nullable=True)
    taxes = Column(Numeric(20, 8), nullable=True)
    total = Column(Numeric(20, 8), nullable=True)

    # Forex locking (INV-02, DG-002)
    forex_rate_at_submission = Column(Numeric(20, 8), nullable=True)
    forex_rate_currency_pair = Column(String(7), nullable=True)  # e.g. "USD/EUR"
    terms_hash = Column(String(128), nullable=True)

    valid_until = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), default=_now)
    response_payload = Column(JSONB, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    rfq = relationship("RFQBatch", back_populates="quote_headers")
    vendor = relationship("Vendor")
    lines = relationship("RFQQuoteLine", back_populates="header", cascade="all, delete-orphan")


class RFQQuoteLine(Base):
    __tablename__ = "rfq_quote_lines"
    __table_args__ = (
        Index("ix_rql_header", "quote_header_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    quote_header_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_quote_headers.id", ondelete="CASCADE"),
        nullable=False,
    )
    rfq_item_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    bom_line_id = Column(UUID(as_uuid=False), nullable=True)  # lineage (DG-006)
    part_name = Column(Text, nullable=True)
    quantity = Column(Numeric(20, 8), nullable=False, default=1)
    unit_price = Column(Numeric(20, 8), nullable=True)
    line_currency = Column(String(3), nullable=False, default="USD")
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    moq = Column(Numeric(20, 8), nullable=True)
    notes = Column(Text, nullable=True)
    line_payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    header = relationship("RFQQuoteHeader", back_populates="lines")


# ── Purchase Order (SM-006) ─────────────────────────────────────────────────

class PurchaseOrder(Base):
    """
    PO entity. Status follows POStatus enum (SM-006, 15 states):
    PO_APPROVED → PO_SENT → VENDOR_ACCEPTED → … → CLOSED | CANCELLED
    """
    __tablename__ = "purchase_orders"
    __table_args__ = (
        Index("ix_po_project", "project_id"),
        Index("ix_po_org", "organization_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    rfq_batch_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    po_number = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="PO_APPROVED")  # POStatus (SM-006)
    total = Column(Numeric(20, 8), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    shipping_terms = Column(Text, nullable=True)
    payment_terms = Column(Text, nullable=True)
    terms_snapshot_json = Column(JSONB, nullable=False, default=dict)

    # Approval
    approved_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # Vendor acknowledgement
    vendor_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    sla_response_deadline = Column(DateTime(timezone=True), nullable=True)  # 48h from PO_SENT

    issued_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    po_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    line_items = relationship("POLineItem", back_populates="po", cascade="all, delete-orphan")
    vendor = relationship("Vendor")


class POLineItem(Base):
    __tablename__ = "po_line_items"
    __table_args__ = (
        Index("ix_poli_po", "po_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    bom_part_id = Column(UUID(as_uuid=False), nullable=True)  # legacy
    bom_line_id = Column(UUID(as_uuid=False), nullable=True)  # canonical lineage (DG-006)
    quote_line_id = Column(UUID(as_uuid=False), nullable=True)  # lineage
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(20, 8), nullable=False, default=1)
    unit_price = Column(Numeric(20, 8), nullable=True)
    total_price = Column(Numeric(20, 8), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    po = relationship("PurchaseOrder", back_populates="line_items")


# ── Invoice (SM-008) ────────────────────────────────────────────────────────

class Invoice(Base):
    """
    Invoice entity. Status follows InvoiceStatus enum (SM-008):
    RECEIVED → VALIDATING → VALIDATED → APPROVED → PAYMENT_PENDING → PAID | DISPUTED | CANCELLED
    """
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_inv_po", "po_id"),
        Index("ix_inv_org", "organization_id"),
        {"schema": "finance"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    vendor_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_number = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="RECEIVED")  # InvoiceStatus (SM-008)
    amount = Column(Numeric(20, 8), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    due_date = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # Three-way match (PO vs GR vs Invoice)
    three_way_match_result = Column(JSONB, nullable=False, default=dict)
    matched_at = Column(DateTime(timezone=True), nullable=True)
    dispute_reason = Column(Text, nullable=True)
    dispute_resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        Index("ix_invl_invoice", "invoice_id"),
        {"schema": "finance"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    invoice_id = Column(
        UUID(as_uuid=False),
        ForeignKey("finance.invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_line_id = Column(UUID(as_uuid=False), nullable=True)
    bom_line_id = Column(UUID(as_uuid=False), nullable=True)  # lineage
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(20, 8), nullable=True)
    unit_price = Column(Numeric(20, 8), nullable=True)
    line_total = Column(Numeric(20, 8), nullable=True)
    currency_code = Column(String(3), nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), default=_now)

    invoice = relationship("Invoice", back_populates="lines")


# ── Payment ──────────────────────────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_pay_invoice", "invoice_id"),
        Index("ix_pay_org", "organization_id"),
        {"schema": "finance"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    invoice_id = Column(
        UUID(as_uuid=False),
        ForeignKey("finance.invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    amount = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    method = Column(Text, nullable=True)
    payment_method = Column(String(40), nullable=True)  # wire, ach, card, erp_sync
    payment_reference = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="PENDING")  # PaymentStatus
    erp_sync_status = Column(String(40), nullable=True)
    erp_reference = Column(Text, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


# ── Goods Receipt ────────────────────────────────────────────────────────────

class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (
        Index("ix_gr_po", "po_id"),
        Index("ix_gr_org", "organization_id"),
        {"schema": "finance"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(String(40), nullable=False, default="PENDING")  # PENDING, ACCEPTED, REJECTED, PARTIAL
    received_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    lines = relationship("GoodsReceiptLine", back_populates="goods_receipt", cascade="all, delete-orphan")


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"
    __table_args__ = (
        Index("ix_grl_gr", "goods_receipt_id"),
        {"schema": "finance"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    goods_receipt_id = Column(
        UUID(as_uuid=False),
        ForeignKey("finance.goods_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_line_id = Column(UUID(as_uuid=False), nullable=True)
    bom_line_id = Column(UUID(as_uuid=False), nullable=True)  # lineage
    expected_quantity = Column(Numeric(20, 8), nullable=True)
    received_quantity = Column(Numeric(20, 8), nullable=True)
    accepted_quantity = Column(Numeric(20, 8), nullable=True)
    discrepancy_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    goods_receipt = relationship("GoodsReceipt", back_populates="lines")


# ── Approval Request ─────────────────────────────────────────────────────────

class ApprovalRequest(Base):
    """Approval workflow entity. Status follows ApprovalStatus enum."""
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_ar_entity", "entity_type", "entity_id"),
        Index("ix_ar_org", "organization_id"),
        Index("ix_ar_assigned", "assigned_to_user_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    entity_type = Column(String(40), nullable=False)  # purchase_order, rfq_award
    entity_id = Column(UUID(as_uuid=False), nullable=False)
    organization_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    assigned_to_user_id = Column(UUID(as_uuid=False), nullable=True)
    status = Column(String(40), nullable=False, default="PENDING")  # ApprovalStatus
    threshold_amount = Column(Numeric(20, 8), nullable=True)
    decision = Column(String(40), nullable=True)  # approved, rejected
    decided_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    reason = Column(Text, nullable=True)
    escalated_to_user_id = Column(UUID(as_uuid=False), nullable=True)
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)