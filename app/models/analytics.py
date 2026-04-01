"""Spend ledger and analytics persistence."""
import uuid
from datetime import datetime

from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Integer, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class SpendLedger(Base):
    __tablename__ = "spend_ledger"
    __table_args__ = (
        Index("ix_spend_ledger_project", "project_id"),
        Index("ix_spend_ledger_vendor", "vendor_id"),
        Index("ix_spend_ledger_category", "category"),
        Index("ix_spend_ledger_occurred_at", "occurred_at"),
        Index("uq_spend_ledger_dedupe", "source_type", "source_id", "ledger_type", "category", unique=True),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="SET NULL"), nullable=True)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="SET NULL"), nullable=True)

    ledger_type = Column(Text, nullable=False)  # committed | invoiced | paid | adjustment
    source_type = Column(Text, nullable=False)   # purchase_order | invoice | payment | receipt | shipment
    source_id = Column(Text, nullable=False)
    category = Column(Text, nullable=False, default="uncategorized")
    region = Column(Text, nullable=True)
    currency = Column(Text, nullable=False, default="USD")

    quantity = Column(Numeric(18, 6), nullable=True)
    unit_price = Column(Numeric(18, 6), nullable=True)
    amount = Column(Numeric(18, 6), nullable=False, default=0)
    baseline_amount = Column(Numeric(18, 6), nullable=True)
    realized_savings = Column(Numeric(18, 6), nullable=True)

    occurred_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project")
    rfq = relationship("RFQBatch")
    vendor = relationship("Vendor")
    purchase_order = relationship("PurchaseOrder")
    shipment = relationship("Shipment")
    invoice = relationship("Invoice")


class CategorySpendRollup(Base):
    __tablename__ = "category_spend_rollups"
    __table_args__ = (
        Index("ix_category_spend_rollups_project", "project_id"),
        Index("ix_category_spend_rollups_period", "period_month"),
        Index("ix_category_spend_rollups_category", "category"),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    period_month = Column(DateTime(timezone=True), nullable=False)
    category = Column(Text, nullable=False)
    currency = Column(Text, nullable=False, default="USD")
    committed_spend = Column(Numeric(18, 6), nullable=False, default=0)
    invoiced_spend = Column(Numeric(18, 6), nullable=False, default=0)
    paid_spend = Column(Numeric(18, 6), nullable=False, default=0)
    savings_realized = Column(Numeric(18, 6), nullable=False, default=0)
    line_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class VendorSpendRollup(Base):
    __tablename__ = "vendor_spend_rollups"
    __table_args__ = (
        Index("ix_vendor_spend_rollups_project", "project_id"),
        Index("ix_vendor_spend_rollups_period", "period_month"),
        Index("ix_vendor_spend_rollups_vendor", "vendor_id"),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    vendor_name = Column(Text, nullable=True)
    period_month = Column(DateTime(timezone=True), nullable=False)
    currency = Column(Text, nullable=False, default="USD")
    committed_spend = Column(Numeric(18, 6), nullable=False, default=0)
    invoiced_spend = Column(Numeric(18, 6), nullable=False, default=0)
    paid_spend = Column(Numeric(18, 6), nullable=False, default=0)
    savings_realized = Column(Numeric(18, 6), nullable=False, default=0)
    total_orders = Column(Integer, nullable=False, default=0)
    on_time_shipments = Column(Integer, nullable=False, default=0)
    late_shipments = Column(Integer, nullable=False, default=0)
    avg_lead_time_days = Column(Numeric(18, 6), nullable=True)
    on_time_rate = Column(Numeric(18, 6), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class MonthlySpendSnapshot(Base):
    __tablename__ = "monthly_spend_snapshots"
    __table_args__ = (
        Index("ix_monthly_spend_snapshots_project", "project_id"),
        Index("ix_monthly_spend_snapshots_period", "period_month"),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    period_month = Column(DateTime(timezone=True), nullable=False)
    currency = Column(Text, nullable=False, default="USD")
    committed_spend = Column(Numeric(18, 6), nullable=False, default=0)
    invoiced_spend = Column(Numeric(18, 6), nullable=False, default=0)
    paid_spend = Column(Numeric(18, 6), nullable=False, default=0)
    savings_realized = Column(Numeric(18, 6), nullable=False, default=0)
    quote_to_order_conversion = Column(Numeric(18, 6), nullable=True)
    vendor_on_time_rate = Column(Numeric(18, 6), nullable=True)
    avg_lead_time_days = Column(Numeric(18, 6), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class SavingsRealized(Base):
    __tablename__ = "savings_realized"
    __table_args__ = (
        Index("ix_savings_realized_project", "project_id"),
        Index("ix_savings_realized_vendor", "vendor_id"),
        Index("ix_savings_realized_source", "source_type", "source_id", unique=True),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    rfq_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="SET NULL"), nullable=True)

    source_type = Column(Text, nullable=False)
    source_id = Column(Text, nullable=False)
    currency = Column(Text, nullable=False, default="USD")
    baseline_amount = Column(Numeric(18, 6), nullable=True)
    actual_amount = Column(Numeric(18, 6), nullable=True)
    realized_amount = Column(Numeric(18, 6), nullable=True)
    realized_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class DeliveryPerformanceRollup(Base):
    __tablename__ = "delivery_performance_rollups"
    __table_args__ = (
        Index("ix_delivery_perf_project", "project_id"),
        Index("ix_delivery_perf_vendor", "vendor_id"),
        Index("ix_delivery_perf_period", "period_month"),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    vendor_name = Column(Text, nullable=True)
    period_month = Column(DateTime(timezone=True), nullable=False)
    currency = Column(Text, nullable=False, default="USD")
    total_shipments = Column(Integer, nullable=False, default=0)
    on_time_shipments = Column(Integer, nullable=False, default=0)
    late_shipments = Column(Integer, nullable=False, default=0)
    on_time_rate = Column(Numeric(18, 6), nullable=True)
    avg_lead_time_days = Column(Numeric(18, 6), nullable=True)
    avg_delay_days = Column(Numeric(18, 6), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class ReportSchedule(Base):
    __tablename__ = "report_schedules"
    __table_args__ = (
        Index("ix_report_schedules_active", "is_active"),
        Index("ix_report_schedules_type", "report_type"),
        {"schema": "analytics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_name = Column(Text, nullable=False)
    report_type = Column(Text, nullable=False)
    frequency = Column(Text, nullable=False, default="weekly")
    recipients_json = Column(JSONB, nullable=False, default=list)
    filters_json = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)