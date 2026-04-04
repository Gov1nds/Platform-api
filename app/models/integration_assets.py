"""Integration and lineage data objects for procurement data sources.

These tables capture the external data feeds and operational records that are
needed for real-world procurement operation but are intentionally separated
from the BOM intelligence engine.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, Text, DateTime, ForeignKey, Boolean, Integer, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class VendorContact(Base):
    __tablename__ = "vendor_contacts"
    __table_args__ = (
        Index("ix_vendor_contacts_vendor", "vendor_id"),
        Index("ix_vendor_contacts_email", "email"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    full_name = Column(Text, nullable=False)
    job_title = Column(Text, nullable=True)
    email = Column(Text, nullable=True)
    phone = Column(Text, nullable=True)
    department = Column(Text, nullable=True)
    channels_json = Column(JSONB, nullable=False, default=list)
    is_primary = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", backref="contacts")


class VendorOperationalProfile(Base):
    __tablename__ = "vendor_operational_profiles"
    __table_args__ = (
        Index("ix_vendor_operational_profiles_vendor", "vendor_id"),
        Index("ix_vendor_operational_profiles_region", "default_region"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False, unique=True)
    default_currency = Column(Text, nullable=False, default="USD")
    default_incoterms = Column(Text, nullable=True)
    freight_terms = Column(Text, nullable=True)
    payment_terms = Column(Text, nullable=True)
    default_quote_valid_days = Column(Integer, nullable=True)
    sample_orders_supported = Column(Boolean, nullable=False, default=False)
    quality_rating = Column(Numeric(6, 3), nullable=True)
    logistics_capability = Column(Numeric(6, 3), nullable=True)
    capacity_notes = Column(Text, nullable=True)
    default_region = Column(Text, nullable=True)
    regions_served = Column(JSONB, nullable=False, default=list)
    moq_by_process = Column(JSONB, nullable=False, default=dict)
    moq_by_part = Column(JSONB, nullable=False, default=dict)
    lead_time_by_process = Column(JSONB, nullable=False, default=dict)
    quote_validity_policy = Column(JSONB, nullable=False, default=dict)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", backref="operational_profile", uselist=False)


class VendorComplianceRefresh(Base):
    __tablename__ = "vendor_compliance_refreshes"
    __table_args__ = (
        Index("ix_vendor_compliance_refresh_vendor", "vendor_id"),
        Index("ix_vendor_compliance_refresh_status", "status"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False)
    certification_name = Column(Text, nullable=False)
    certification_id = Column(Text, nullable=True)
    issued_by = Column(Text, nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Text, nullable=False, default="active")
    source_url = Column(Text, nullable=True)
    source_snapshot_id = Column(UUID(as_uuid=False), nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    observed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", backref="compliance_refreshes")


class ExternalFeedSnapshot(Base):
    __tablename__ = "external_feed_snapshots"
    __table_args__ = (
        Index("ix_external_feed_snapshots_vendor", "vendor_id"),
        Index("ix_external_feed_snapshots_feed_type", "feed_type"),
        Index("ix_external_feed_snapshots_external_id", "external_id"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    feed_type = Column(Text, nullable=False)  # catalog | pricing | logistics | tariff | compliance
    source_name = Column(Text, nullable=False)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    external_id = Column(Text, nullable=True)
    external_part_number = Column(Text, nullable=True)
    canonical_part_key = Column(Text, nullable=True)
    part_name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    source_currency = Column(Text, nullable=False, default="USD")
    unit_price = Column(Numeric(18, 6), nullable=True)
    moq = Column(Numeric(18, 6), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    incoterms = Column(Text, nullable=True)
    freight_terms = Column(Text, nullable=True)
    tax_region = Column(Text, nullable=True)
    duty_region = Column(Text, nullable=True)
    quote_valid_until = Column(DateTime(timezone=True), nullable=True)
    availability_status = Column(Text, nullable=True)
    compliance_status = Column(Text, nullable=True)
    region = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    source_payload = Column(JSONB, nullable=False, default=dict)
    normalized_payload = Column(JSONB, nullable=False, default=dict)
    observed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor", backref="feed_snapshots")


class DocumentAsset(Base):
    __tablename__ = "document_assets"
    __table_args__ = (
        Index("ix_document_assets_source", "source_type", "source_id"),
        Index("ix_document_assets_project", "project_id"),
        Index("ix_document_assets_bom", "bom_id"),
        Index("ix_document_assets_rfq", "rfq_batch_id"),
        Index("ix_document_assets_vendor", "vendor_id"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type = Column(Text, nullable=False)  # drawing | attachment | upload | invoice | po | quote | cert | tariff_doc
    source_id = Column(Text, nullable=False)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True)
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="SET NULL"), nullable=True)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="SET NULL"), nullable=True)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="SET NULL"), nullable=True)

    storage_provider = Column(Text, nullable=False, default="local")
    storage_key = Column(Text, nullable=False)
    public_url = Column(Text, nullable=True)
    file_name = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    sha256 = Column(Text, nullable=True)
    version_no = Column(Integer, nullable=False, default=1)
    revision_label = Column(Text, nullable=True)
    is_current = Column(Boolean, nullable=False, default=True)
    asset_status = Column(Text, nullable=False, default="stored")
    asset_kind = Column(Text, nullable=False, default="generic")
    uploaded_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project")
    bom = relationship("BOM")
    rfq = relationship("RFQBatch")
    vendor = relationship("Vendor")
    purchase_order = relationship("PurchaseOrder")
    shipment = relationship("Shipment")
    invoice = relationship("Invoice")
    uploaded_by = relationship("User")


class BOMRevisionLink(Base):
    __tablename__ = "bom_revision_links"
    __table_args__ = (
        Index("ix_bom_revision_links_parent", "parent_bom_id"),
        Index("ix_bom_revision_links_child", "child_bom_id"),
        Index("ix_bom_revision_links_status", "approval_status"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    child_bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    source_drawing_asset_id = Column(UUID(as_uuid=False), ForeignKey("integrations.document_assets.id", ondelete="SET NULL"), nullable=True)
    source_document_asset_id = Column(UUID(as_uuid=False), ForeignKey("integrations.document_assets.id", ondelete="SET NULL"), nullable=True)
    revision_no = Column(Integer, nullable=False, default=1)
    revision_label = Column(Text, nullable=True)
    change_summary = Column(Text, nullable=True)
    approval_status = Column(Text, nullable=False, default="pending")
    approved_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class AlternatePartApproval(Base):
    __tablename__ = "alternate_part_approvals"
    __table_args__ = (
        Index("ix_alternate_part_approvals_bom_part", "bom_part_id"),
        Index("ix_alternate_part_approvals_status", "approval_status"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False)
    alternate_part_key = Column(Text, nullable=True)
    alternate_mpn = Column(Text, nullable=True)
    alternate_manufacturer = Column(Text, nullable=True)
    approval_status = Column(Text, nullable=False, default="pending")
    approval_reason = Column(Text, nullable=True)
    approved_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    effective_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class TrackingNumberHistory(Base):
    __tablename__ = "tracking_number_history"
    __table_args__ = (
        Index("ix_tracking_number_history_shipment", "shipment_id"),
        Index("ix_tracking_number_history_number", "tracking_number"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="CASCADE"), nullable=False)
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="SET NULL"), nullable=True)
    carrier_name = Column(Text, nullable=True)
    carrier_code = Column(Text, nullable=True)
    tracking_number = Column(Text, nullable=False)
    tracking_number_source = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="active")
    effective_from = Column(DateTime(timezone=True), default=datetime.utcnow)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class GoodsReceiptReconciliation(Base):
    __tablename__ = "goods_receipt_reconciliations"
    __table_args__ = (
        Index("ix_goods_receipt_recon_po", "purchase_order_id"),
        Index("ix_goods_receipt_recon_receipt", "goods_receipt_id"),
        Index("ix_goods_receipt_recon_status", "reconciliation_status"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("ops.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("ops.shipments.id", ondelete="SET NULL"), nullable=True)
    goods_receipt_id = Column(UUID(as_uuid=False), ForeignKey("ops.goods_receipts.id", ondelete="CASCADE"), nullable=False)
    invoice_id = Column(UUID(as_uuid=False), ForeignKey("ops.invoices.id", ondelete="SET NULL"), nullable=True)
    reconciliation_status = Column(Text, nullable=False, default="pending")
    matched_quantity = Column(Numeric(18, 6), nullable=True)
    matched_amount = Column(Numeric(18, 6), nullable=True)
    variance_amount = Column(Numeric(18, 6), nullable=True)
    matched_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailIngestMessage(Base):
    __tablename__ = "email_ingest_messages"
    __table_args__ = (
        Index("ix_email_ingest_messages_message_id", "message_id"),
        Index("ix_email_ingest_messages_rfq", "rfq_batch_id"),
        Index("ix_email_ingest_messages_project", "project_id"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = Column(Text, nullable=False)
    thread_token = Column(Text, nullable=True)
    from_email = Column(Text, nullable=True)
    to_email = Column(Text, nullable=True)
    subject = Column(Text, nullable=True)
    body_text = Column(Text, nullable=True)
    raw_headers_json = Column(JSONB, nullable=False, default=dict)
    attachment_count = Column(Integer, nullable=False, default=0)
    parsed_status = Column(Text, nullable=False, default="received")
    parse_summary = Column(JSONB, nullable=False, default=dict)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    received_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class IntegrationEvent(Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        Index("ix_integration_events_event_type", "event_type"),
        Index("ix_integration_events_source", "source_system"),
        Index("ix_integration_events_status", "status"),
        {"schema": "integrations"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(Text, nullable=False)  # api_error | job_failure | webhook_received | webhook_failed | sync_started | sync_completed
    source_system = Column(Text, nullable=False)
    target_system = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="received")
    severity = Column(Text, nullable=False, default="info")
    correlation_id = Column(Text, nullable=True)
    external_reference = Column(Text, nullable=True)
    request_method = Column(Text, nullable=True)
    request_path = Column(Text, nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    error_text = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
