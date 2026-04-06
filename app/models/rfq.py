import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Numeric, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base

def _now(): return datetime.now(timezone.utc)
def _uuid(): return str(uuid.uuid4())

RFQ_STATUSES = ["draft","sent","partial","quoted","approved","rejected","closed"]
INVITATION_STATUSES = ["invited","opened","questions_asked","partially_quoted","fully_quoted","rejected","expired","accepted","negotiated","awarded"]
QUOTE_STATUSES = ["draft","submitted","received","under_review","accepted","rejected","revised","expired"]

class RFQBatch(Base):
    __tablename__ = "rfq_batches"
    __table_args__ = (Index("ix_rfq_project","project_id"),{"schema":"sourcing"})
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    target_currency = Column(String(3), nullable=False, default="USD")
    status = Column(Text, nullable=False, default="draft")
    notes = Column(Text, nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)
    batch_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")
    invitations = relationship("RFQVendorInvitation", back_populates="rfq", cascade="all, delete-orphan")
    quote_headers = relationship("RFQQuoteHeader", back_populates="rfq", cascade="all, delete-orphan")

class RFQItem(Base):
    __tablename__ = "rfq_items"
    __table_args__ = {"schema":"sourcing"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False)
    part_key = Column(Text, nullable=True)
    requested_quantity = Column(Numeric(18,6), nullable=False, default=1)
    requested_material = Column(Text, nullable=True)
    requested_process = Column(Text, nullable=True)
    drawing_required = Column(Boolean, nullable=False, default=False)
    spec_summary = Column(JSONB, nullable=False, default=dict)
    status = Column(Text, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=_now)
    rfq = relationship("RFQBatch", back_populates="items")

class RFQVendorInvitation(Base):
    __tablename__ = "rfq_vendor_invitations"
    __table_args__ = (Index("ix_rvi_rfq","rfq_batch_id"),Index("ix_rvi_vendor","vendor_id"),{"schema":"sourcing"})
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    status = Column(Text, nullable=False, default="invited")
    invited_at = Column(DateTime(timezone=True), default=_now)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    portal_token = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    rfq = relationship("RFQBatch", back_populates="invitations")
    vendor = relationship("Vendor")
    status_history = relationship("InvitationStatusEvent", back_populates="invitation", cascade="all, delete-orphan")

class InvitationStatusEvent(Base):
    __tablename__ = "invitation_status_events"
    __table_args__ = {"schema":"sourcing"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    invitation_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_vendor_invitations.id", ondelete="CASCADE"), nullable=False)
    old_status = Column(Text, nullable=True)
    new_status = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    invitation = relationship("RFQVendorInvitation", back_populates="status_history")

class RFQQuoteHeader(Base):
    __tablename__ = "rfq_quote_headers"
    __table_args__ = (Index("ix_rqh_rfq","rfq_batch_id"),Index("ix_rqh_vendor","vendor_id"),{"schema":"sourcing"})
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    quote_number = Column(Text, nullable=True)
    quote_status = Column(Text, nullable=False, default="received")
    quote_currency = Column(String(3), nullable=False, default="USD")
    incoterms = Column(Text, nullable=True)
    quote_version = Column(Integer, nullable=False, default=1)
    is_revision = Column(Boolean, nullable=False, default=False)
    parent_quote_id = Column(UUID(as_uuid=False), nullable=True)
    acceptance_status = Column(Text, nullable=False, default="pending")
    subtotal = Column(Numeric(18,6), nullable=True)
    freight = Column(Numeric(18,6), nullable=True)
    taxes = Column(Numeric(18,6), nullable=True)
    total = Column(Numeric(18,6), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), default=_now)
    response_payload = Column(JSONB, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    rfq = relationship("RFQBatch", back_populates="quote_headers")
    vendor = relationship("Vendor")
    lines = relationship("RFQQuoteLine", back_populates="header", cascade="all, delete-orphan")

class RFQQuoteLine(Base):
    __tablename__ = "rfq_quote_lines"
    __table_args__ = (Index("ix_rql_header","quote_header_id"),{"schema":"sourcing"})
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    quote_header_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_quote_headers.id", ondelete="CASCADE"), nullable=False)
    rfq_item_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_items.id", ondelete="CASCADE"), nullable=False)
    part_name = Column(Text, nullable=True)
    quantity = Column(Numeric(18,6), nullable=False, default=1)
    unit_price = Column(Numeric(18,6), nullable=True)
    line_currency = Column(String(3), nullable=False, default="USD")
    lead_time_days = Column(Numeric(12,2), nullable=True)
    moq = Column(Numeric(18,6), nullable=True)
    notes = Column(Text, nullable=True)
    line_payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    header = relationship("RFQQuoteHeader", back_populates="lines")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_po_project","project_id"),{"schema":"sourcing"})
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    po_number = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="draft")
    total = Column(Numeric(18,6), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    shipping_terms = Column(Text, nullable=True)
    payment_terms = Column(Text, nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    po_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    line_items = relationship("POLineItem", back_populates="po", cascade="all, delete-orphan")
    vendor = relationship("Vendor")

class POLineItem(Base):
    __tablename__ = "po_line_items"
    __table_args__ = {"schema":"sourcing"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(18,6), nullable=False, default=1)
    unit_price = Column(Numeric(18,6), nullable=True)
    total_price = Column(Numeric(18,6), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    po = relationship("PurchaseOrder", back_populates="line_items")

class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = {"schema":"finance"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), nullable=True)
    invoice_number = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    amount = Column(Numeric(18,6), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    due_date = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = {"schema":"finance"}
    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("finance.invoices.id", ondelete="SET NULL"), nullable=True)
    amount = Column(Numeric(18,6), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    method = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
