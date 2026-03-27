"""RFQ models — maps to sourcing.rfq_batches, rfq_items, rfq_quotes."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Boolean, String
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


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

    user = relationship("User", back_populates="rfqs")
    bom = relationship("BOM", back_populates="rfqs")
    items = relationship("RFQItem", back_populates="rfq", cascade="all, delete-orphan")
    quotes = relationship("RFQQuote", back_populates="rfq", cascade="all, delete-orphan")
    drawings = relationship("DrawingAsset", back_populates="rfq", cascade="all, delete-orphan",
                            primaryjoin="RFQBatch.id == foreign(DrawingAsset.rfq_batch_id)")


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
    """Maps to sourcing.rfq_quotes."""
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
