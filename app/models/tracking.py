"""Production tracking and fulfillment execution models.

These tables are NOT in the bootstrap PostgreSQL schema. They are created
by the supplementary migration (002_add_tracking_tables.py) plus the new
fulfillment migration for order/shipment/invoice/payment objects.
We keep them in the 'ops' schema to align with the bootstrap conventions.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    Numeric,
    Boolean,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class TrackingStage(str, enum.Enum):
    T0 = "T0"  # Legacy: order placed
    T1 = "T1"  # Legacy: material procurement
    T2 = "T2"  # Legacy: manufacturing started
    T3 = "T3"  # Legacy: QC / inspection
    T4 = "T4"  # Legacy: shipped / delivered


class FulfillmentState(str, enum.Enum):
    rfq_sent = "rfq_sent"
    quote_received = "quote_received"
    quote_accepted = "quote_accepted"
    po_issued = "po_issued"
    order_confirmed = "order_confirmed"
    production_started = "production_started"
    qc_passed = "qc_passed"
    shipped = "shipped"
    in_transit = "in_transit"
    customs = "customs"
    delivered = "delivered"
    receipt_confirmed = "receipt_confirmed"
    invoice_matched = "invoice_matched"
    paid = "paid"
    closed = "closed"
    delayed = "delayed"
    cancelled = "cancelled"


class ProductionTracking(Base):
    __tablename__ = "production_tracking"
    __table_args__ = (
        Index("ix_production_tracking_rfq_id", "rfq_id"),
        Index("ix_production_tracking_created_at", "created_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False, index=True)

    # Legacy stage progression preserved
    stage = Column(Text, default=TrackingStage.T0.value)

    # New execution-aware state
    execution_state = Column(Text, nullable=False, default=FulfillmentState.rfq_sent.value)

    status_message = Column(Text, nullable=True)
    progress_percent = Column(Integer, default=0)
    updated_by = Column(Text, nullable=True)

    # direct pointers to fulfillment entities
    po_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="SET NULL"), nullable=True)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="SET NULL"), nullable=True)

    delay_reason = Column(Text, nullable=True)
    context_json = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", foreign_keys=[rfq_id])
    purchase_order = relationship("PurchaseOrder", foreign_keys=[po_id])
    shipment = relationship("Shipment", foreign_keys=[shipment_id])
    invoice = relationship("Invoice", foreign_keys=[invoice_id])


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        Index("ix_purchase_orders_rfq", "rfq_id"),
        Index("ix_purchase_orders_project", "project_id"),
        Index("ix_purchase_orders_vendor", "vendor_id"),
        Index("ix_purchase_orders_status", "status"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)

    po_number = Column(Text, nullable=False, unique=True)
    status = Column(Text, nullable=False, default=FulfillmentState.po_issued.value)
    vendor_confirmation_status = Column(Text, nullable=False, default="pending")
    vendor_confirmation_number = Column(Text, nullable=True)

    issued_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    currency = Column(Text, nullable=False, default="USD")
    subtotal = Column(Numeric(18, 6), nullable=True)
    freight = Column(Numeric(18, 6), nullable=True)
    taxes = Column(Numeric(18, 6), nullable=True)
    total_amount = Column(Numeric(18, 6), nullable=True)

    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch")
    vendor = relationship("Vendor")
    project = relationship("Project")
    shipments = relationship("Shipment", back_populates="purchase_order", cascade="all, delete-orphan")
    goods_receipts = relationship("GoodsReceipt", back_populates="purchase_order", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="purchase_order", cascade="all, delete-orphan")
    tracking_rows = relationship("ProductionTracking", back_populates="purchase_order")


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        Index("ix_shipments_po", "purchase_order_id"),
        Index("ix_shipments_status", "status"),
        Index("ix_shipments_tracking_number", "tracking_number"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    shipment_number = Column(Text, nullable=False, unique=True)

    carrier_name = Column(Text, nullable=True)
    carrier_code = Column(Text, nullable=True)
    tracking_number = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default=FulfillmentState.shipped.value)

    shipped_at = Column(DateTime(timezone=True), nullable=True)
    eta = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    delay_reason = Column(Text, nullable=True)

    origin = Column(Text, nullable=True)
    destination = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    purchase_order = relationship("PurchaseOrder", back_populates="shipments")
    events = relationship("ShipmentEvent", back_populates="shipment", cascade="all, delete-orphan")
    milestones = relationship("CarrierMilestone", back_populates="shipment", cascade="all, delete-orphan")
    customs_events = relationship("CustomsEvent", back_populates="shipment", cascade="all, delete-orphan")
    receipts = relationship("GoodsReceipt", back_populates="shipment", cascade="all, delete-orphan")
    tracking_rows = relationship("ProductionTracking", back_populates="shipment")


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    __table_args__ = (
        Index("ix_shipment_events_shipment", "shipment_id"),
        Index("ix_shipment_events_created", "occurred_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(Text, nullable=False)
    event_status = Column(Text, nullable=False, default="recorded")
    location = Column(Text, nullable=True)
    message = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    shipment = relationship("Shipment", back_populates="events")


class CarrierMilestone(Base):
    __tablename__ = "carrier_milestones"
    __table_args__ = (
        Index("ix_carrier_milestones_shipment", "shipment_id"),
        Index("ix_carrier_milestones_code", "milestone_code"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="CASCADE"), nullable=False)
    milestone_code = Column(Text, nullable=False)
    milestone_name = Column(Text, nullable=False)
    milestone_status = Column(Text, nullable=False, default="pending")
    description = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    estimated_at = Column(DateTime(timezone=True), nullable=True)
    actual_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="milestones")


class CustomsEvent(Base):
    __tablename__ = "customs_events"
    __table_args__ = (
        Index("ix_customs_events_shipment", "shipment_id"),
        Index("ix_customs_events_status", "status"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="CASCADE"), nullable=False)
    country = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    message = Column(Text, nullable=True)
    held_reason = Column(Text, nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="customs_events")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (
        Index("ix_goods_receipts_po", "purchase_order_id"),
        Index("ix_goods_receipts_shipment", "shipment_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="SET NULL"), nullable=True)

    receipt_number = Column(Text, nullable=False, unique=True)
    receipt_status = Column(Text, nullable=False, default="pending")
    received_quantity = Column(Numeric(18, 6), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    purchase_order = relationship("PurchaseOrder", back_populates="goods_receipts")
    shipment = relationship("Shipment", back_populates="receipts")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_po", "purchase_order_id"),
        Index("ix_invoices_vendor", "vendor_id"),
        Index("ix_invoices_status", "invoice_status"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)

    invoice_number = Column(Text, nullable=False, unique=True)
    invoice_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    invoice_status = Column(Text, nullable=False, default="issued")
    currency = Column(Text, nullable=False, default="USD")
    subtotal = Column(Numeric(18, 6), nullable=True)
    taxes = Column(Numeric(18, 6), nullable=True)
    total_amount = Column(Numeric(18, 6), nullable=True)
    matched_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    purchase_order = relationship("PurchaseOrder", back_populates="invoices")
    vendor = relationship("Vendor")
    payment_state = relationship("PaymentState", back_populates="invoice", uselist=False, cascade="all, delete-orphan")
    tracking_rows = relationship("ProductionTracking", back_populates="invoice")


class PaymentState(Base):
    __tablename__ = "payment_states"
    __table_args__ = (
        Index("ix_payment_states_invoice", "invoice_id"),
        Index("ix_payment_states_status", "status"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="CASCADE"), nullable=False, unique=True)
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="CASCADE"), nullable=False)

    status = Column(Text, nullable=False, default="unpaid")  # unpaid | pending | matched | paid | failed | closed
    paid_at = Column(DateTime(timezone=True), nullable=True)
    payment_reference = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="payment_state")
    purchase_order = relationship("PurchaseOrder")


class ExecutionFeedback(Base):
    __tablename__ = "execution_feedback"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False, unique=True)
    predicted_cost = Column(Numeric(18, 6), nullable=True)
    actual_cost = Column(Numeric(18, 6), nullable=True)
    cost_delta = Column(Numeric(18, 6), nullable=True)
    predicted_lead_time = Column(Numeric(12, 2), nullable=True)
    actual_lead_time = Column(Numeric(12, 2), nullable=True)
    lead_time_delta = Column(Numeric(12, 2), nullable=True)
    feedback_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQBatch", foreign_keys=[rfq_id])