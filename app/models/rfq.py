"""RFQ models — normalized quote lifecycle + backward-compatible legacy quote support."""
import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Boolean, String, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class RFQStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    partial = "partial"
    quoted = "quoted"
    approved = "approved"
    rejected = "rejected"
    closed = "closed"
    error = "error"

    # Backward compat aliases
    created = "draft"
    in_production = "approved"
    completed = "closed"


class RFQBatch(Base):
    """Maps to sourcing.rfq_batches."""
    __tablename__ = "rfq_batches"
    __table_args__ = {"schema": "sourcing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    target_currency = Column(String(3), nullable=False, default="USD")
    status = Column(Text, nullable=False, default="draft")
    notes = Column(Text, nullable=True)
    batch_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def user_id(self):
        return self.requested_by_user_id

    @user_id.setter
    def user_id(self, value):
        self.requested_by_user_id = value

    @property
    def currency(self):
        return self.target_currency

    @currency.setter
    def currency(self, value):
        self.target_currency = value or "USD"

    @property
    def total_estimated_cost(self):
        return (self.batch_metadata or {}).get("total_estimated_cost")

    @total_estimated_cost.setter
    def total_estimated_cost(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["total_estimated_cost"] = value

    @property
    def total_final_cost(self):
        return (self.batch_metadata or {}).get("total_final_cost")

    @total_final_cost.setter
    def total_final_cost(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["total_final_cost"] = value

    @property
    def selected_vendor_id(self):
        return (self.batch_metadata or {}).get("selected_vendor_id")

    @selected_vendor_id.setter
    def selected_vendor_id(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["selected_vendor_id"] = value

    @property
    def vendor_response_deadline(self):
        return (self.batch_metadata or {}).get("vendor_response_deadline")

    @vendor_response_deadline.setter
    def vendor_response_deadline(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["vendor_response_deadline"] = value

    @property
    def sent_at(self):
        return (self.batch_metadata or {}).get("sent_at")

    @sent_at.setter
    def sent_at(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["sent_at"] = value

    @property
    def received_at(self):
        return (self.batch_metadata or {}).get("received_at")

    @received_at.setter
    def received_at(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["received_at"] = value

    @property
    def expires_at(self):
        return (self.batch_metadata or {}).get("expires_at")

    @expires_at.setter
    def expires_at(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["expires_at"] = value

    @property
    def response_status(self):
        return (self.batch_metadata or {}).get("response_status")

    @response_status.setter
    def response_status(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["response_status"] = value

    @property
    def quote_status(self):
        return (self.batch_metadata or {}).get("quote_status", self.status)

    @quote_status.setter
    def quote_status(self, value):
        if not self.batch_metadata:
            self.batch_metadata = {}
        self.batch_metadata["quote_status"] = value

    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")

    # Legacy quote table
    quotes = relationship("RFQQuote", back_populates="rfq", cascade="all, delete-orphan")

    # Normalized quote lifecycle tables
    quote_headers = relationship("RFQQuoteHeader", back_populates="rfq", cascade="all, delete-orphan")
    comparison_views = relationship("RFQComparisonView", back_populates="rfq", cascade="all, delete-orphan")

    drawings = relationship(
        "DrawingAsset",
        back_populates="rfq",
        cascade="all, delete-orphan",
        primaryjoin="RFQBatch.id == foreign(DrawingAsset.rfq_batch_id)",
    )


# Backward compat alias
RFQ = RFQBatch


class RFQItem(Base):
    """Maps to sourcing.rfq_items."""
    __tablename__ = "rfq_items"
    __table_args__ = {"schema": "sourcing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False)
    part_key = Column(Text, nullable=True)
    requested_quantity = Column(Numeric(18, 6), nullable=False, default=1)
    requested_process = Column(Text, nullable=True)
    requested_material = Column(Text, nullable=True)
    requested_due_date = Column(DateTime, nullable=True)
    drawing_required = Column(Boolean, nullable=False, default=False)
    spec_summary = Column(JSONB, nullable=False, default=dict)
    status = Column(Text, nullable=False, default="pending")
    canonical_part_key = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def rfq_id(self):
        return self.rfq_batch_id

    @rfq_id.setter
    def rfq_id(self, value):
        self.rfq_batch_id = value

    @property
    def part_name(self):
        return self.part_key or ""

    @part_name.setter
    def part_name(self, value):
        self.part_key = value

    @property
    def quantity(self):
        return int(self.requested_quantity) if self.requested_quantity else 1

    @quantity.setter
    def quantity(self, value):
        self.requested_quantity = value or 1

    @property
    def material(self):
        return self.requested_material or ""

    @material.setter
    def material(self, value):
        self.requested_material = value

    @property
    def process(self):
        return self.requested_process

    @process.setter
    def process(self, value):
        self.requested_process = value

    @property
    def quoted_price(self):
        return (self.spec_summary or {}).get("quoted_price")

    @quoted_price.setter
    def quoted_price(self, value):
        if not self.spec_summary:
            self.spec_summary = {}
        self.spec_summary["quoted_price"] = value

    @property
    def final_price(self):
        return (self.spec_summary or {}).get("final_price")

    @final_price.setter
    def final_price(self, value):
        if not self.spec_summary:
            self.spec_summary = {}
        self.spec_summary["final_price"] = value

    @property
    def lead_time(self):
        return (self.spec_summary or {}).get("lead_time")

    @lead_time.setter
    def lead_time(self, value):
        if not self.spec_summary:
            self.spec_summary = {}
        self.spec_summary["lead_time"] = value

    rfq = relationship("RFQBatch", back_populates="items")


class RFQQuote(Base):
    """Legacy quote table — retained for backward compatibility."""
    __tablename__ = "rfq_quotes"
    __table_args__ = {"schema": "sourcing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    quote_number = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="received")
    quote_currency = Column(String(3), nullable=False, default="USD")
    subtotal = Column(Numeric(18, 6), nullable=True)
    freight = Column(Numeric(18, 6), nullable=True)
    taxes = Column(Numeric(18, 6), nullable=True)
    total = Column(Numeric(18, 6), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    source_snapshot_id = Column(UUID(as_uuid=False), nullable=True)
    response_payload = Column(JSONB, nullable=False, default=dict)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward compat
    @property
    def rfq_id(self):
        return self.rfq_batch_id

    rfq = relationship("RFQBatch", back_populates="quotes")


class RFQQuoteHeader(Base):
    """Normalized vendor quote header for comparison and audit."""
    __tablename__ = "rfq_quote_headers"
    __table_args__ = (
        Index("ix_rfq_quote_headers_rfq", "rfq_batch_id"),
        Index("ix_rfq_quote_headers_vendor", "vendor_id"),
        Index("ix_rfq_quote_headers_status", "quote_status"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)

    quote_number = Column(Text, nullable=True)
    quote_status = Column(Text, nullable=False, default="received")
    response_status = Column(Text, nullable=False, default="received")

    quote_currency = Column(String(3), nullable=False, default="USD")
    subtotal = Column(Numeric(18, 6), nullable=True)
    freight = Column(Numeric(18, 6), nullable=True)
    taxes = Column(Numeric(18, 6), nullable=True)
    total = Column(Numeric(18, 6), nullable=True)

    vendor_response_deadline = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)

    source_snapshot_id = Column(UUID(as_uuid=False), nullable=True)
    response_payload = Column(JSONB, nullable=False, default=dict)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", back_populates="quote_headers")
    lines = relationship("RFQQuoteLine", back_populates="header", cascade="all, delete-orphan")
    vendor = relationship("Vendor")


class RFQQuoteLine(Base):
    """Normalized line-item quote for matrix comparison."""
    __tablename__ = "rfq_quote_lines"
    __table_args__ = (
        Index("ix_rfq_quote_lines_header", "quote_header_id"),
        Index("ix_rfq_quote_lines_rfq_item", "rfq_item_id"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    quote_header_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_quote_headers.id", ondelete="CASCADE"), nullable=False)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    rfq_item_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_items.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False)

    part_name = Column(Text, nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
    unit_price = Column(Numeric(18, 6), nullable=True)
    lead_time = Column(Numeric(18, 6), nullable=True)

    availability_status = Column(Text, nullable=False, default="unknown")
    compliance_status = Column(Text, nullable=False, default="unknown")
    moq = Column(Numeric(18, 6), nullable=True)
    risk_score = Column(Numeric(12, 6), nullable=True)

    line_payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    header = relationship("RFQQuoteHeader", back_populates="lines")
    rfq = relationship("RFQBatch")
    item = relationship("RFQItem")


class RFQComparisonView(Base):
    """Immutable comparison snapshot for the compare UI."""
    __tablename__ = "rfq_comparison_views"
    __table_args__ = (
        Index("ix_rfq_comparison_views_rfq", "rfq_batch_id"),
        Index("ix_rfq_comparison_views_version", "version"),
        {"schema": "sourcing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    sort_by = Column(Text, nullable=False, default="total_cost")
    filters_json = Column(JSONB, nullable=False, default=dict)
    comparison_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", back_populates="comparison_views")