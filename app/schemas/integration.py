"""Schemas for integration feeds, storage artifacts, and observability events."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class VendorContactSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    vendor_id: str
    full_name: str
    job_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    channels_json: List[Dict[str, Any]] = Field(default_factory=list)
    is_primary: bool = False
    is_active: bool = True
    notes: Optional[str] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VendorOperationalProfileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    vendor_id: str
    default_currency: str = "USD"
    default_incoterms: Optional[str] = None
    freight_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    default_quote_valid_days: Optional[int] = None
    sample_orders_supported: bool = False
    quality_rating: Optional[float] = None
    logistics_capability: Optional[float] = None
    capacity_notes: Optional[str] = None
    default_region: Optional[str] = None
    regions_served: List[str] = Field(default_factory=list)
    moq_by_process: Dict[str, Any] = Field(default_factory=dict)
    moq_by_part: Dict[str, Any] = Field(default_factory=dict)
    lead_time_by_process: Dict[str, Any] = Field(default_factory=dict)
    quote_validity_policy: Dict[str, Any] = Field(default_factory=dict)
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VendorComplianceRefreshSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    vendor_id: str
    certification_name: str
    certification_id: Optional[str] = None
    issued_by: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str = "active"
    source_url: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    observed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExternalFeedSnapshotSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    feed_type: str
    source_name: str
    vendor_id: Optional[str] = None
    external_id: Optional[str] = None
    external_part_number: Optional[str] = None
    canonical_part_key: Optional[str] = None
    part_name: Optional[str] = None
    description: Optional[str] = None
    source_currency: str = "USD"
    unit_price: Optional[float] = None
    moq: Optional[float] = None
    lead_time_days: Optional[float] = None
    incoterms: Optional[str] = None
    freight_terms: Optional[str] = None
    tax_region: Optional[str] = None
    duty_region: Optional[str] = None
    quote_valid_until: Optional[datetime] = None
    availability_status: Optional[str] = None
    compliance_status: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    source_url: Optional[str] = None
    source_payload: Dict[str, Any] = Field(default_factory=dict)
    normalized_payload: Dict[str, Any] = Field(default_factory=dict)
    observed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentAssetSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_type: str
    source_id: str
    project_id: Optional[str] = None
    bom_id: Optional[str] = None
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    purchase_order_id: Optional[str] = None
    shipment_id: Optional[str] = None
    invoice_id: Optional[str] = None
    storage_provider: str = "local"
    storage_key: str
    public_url: Optional[str] = None
    file_name: str
    mime_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    version_no: int = 1
    revision_label: Optional[str] = None
    is_current: bool = True
    asset_status: str = "stored"
    asset_kind: str = "generic"
    uploaded_by_user_id: Optional[str] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AlternatePartApprovalSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bom_id: str
    bom_part_id: str
    alternate_part_key: Optional[str] = None
    alternate_mpn: Optional[str] = None
    alternate_manufacturer: Optional[str] = None
    approval_status: str = "pending"
    approval_reason: Optional[str] = None
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TrackingNumberHistorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    shipment_id: str
    purchase_order_id: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_code: Optional[str] = None
    tracking_number: str
    tracking_number_source: Optional[str] = None
    status: str = "active"
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class GoodsReceiptReconciliationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    purchase_order_id: str
    shipment_id: Optional[str] = None
    goods_receipt_id: str
    invoice_id: Optional[str] = None
    reconciliation_status: str = "pending"
    matched_quantity: Optional[float] = None
    matched_amount: Optional[float] = None
    variance_amount: Optional[float] = None
    matched_at: Optional[datetime] = None
    resolved_by_user_id: Optional[str] = None
    notes: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EmailIngestMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    thread_token: Optional[str] = None
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None
    body_text: Optional[str] = None
    raw_headers_json: Dict[str, Any] = Field(default_factory=dict)
    attachment_count: int = 0
    parsed_status: str = "received"
    parse_summary: Dict[str, Any] = Field(default_factory=dict)
    rfq_batch_id: Optional[str] = None
    project_id: Optional[str] = None
    vendor_id: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    received_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IntegrationEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    source_system: str
    target_system: Optional[str] = None
    status: str = "received"
    severity: str = "info"
    correlation_id: Optional[str] = None
    external_reference: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    error_text: Optional[str] = None
    occurred_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CatalogSyncRequest(BaseModel):
    vendor_id: Optional[str] = None
    source_name: str
    feed_type: str = "catalog"
    source_url: Optional[str] = None
    items: List[Dict[str, Any]] = Field(default_factory=list)


class ComplianceRefreshRequest(BaseModel):
    vendor_id: str
    certification_name: str
    certification_id: Optional[str] = None
    issued_by: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str = "active"
    source_url: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)


class EmailIngestRequest(BaseModel):
    message_id: str
    thread_token: Optional[str] = None
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None
    body_text: Optional[str] = None
    raw_headers_json: Dict[str, Any] = Field(default_factory=dict)
    attachment_count: int = 0
    rfq_batch_id: Optional[str] = None
    project_id: Optional[str] = None
    vendor_id: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)


class WebhookRequest(BaseModel):
    event_type: str
    source_system: str
    target_system: Optional[str] = None
    external_reference: Optional[str] = None
    request_path: Optional[str] = None
    status: str = "received"
    severity: str = "info"
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    error_text: Optional[str] = None


class DocumentUploadRequest(BaseModel):
    source_type: str
    source_id: str
    project_id: Optional[str] = None
    bom_id: Optional[str] = None
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    purchase_order_id: Optional[str] = None
    shipment_id: Optional[str] = None
    invoice_id: Optional[str] = None
    storage_provider: Optional[str] = None
    asset_kind: Optional[str] = None
    revision_label: Optional[str] = None
    is_current: bool = True
    metadata_: Dict[str, Any] = Field(default_factory=dict)


class AlternateApprovalRequest(BaseModel):
    bom_id: str
    bom_part_id: str
    alternate_part_key: Optional[str] = None
    alternate_mpn: Optional[str] = None
    alternate_manufacturer: Optional[str] = None
    approval_status: str = "pending"
    approval_reason: Optional[str] = None
    approved_by_user_id: Optional[str] = None
    effective_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    metadata_: Dict[str, Any] = Field(default_factory=dict)


class GoodsReceiptReconciliationRequest(BaseModel):
    purchase_order_id: str
    shipment_id: Optional[str] = None
    goods_receipt_id: str
    invoice_id: Optional[str] = None
    reconciliation_status: str = "pending"
    matched_quantity: Optional[float] = None
    matched_amount: Optional[float] = None
    variance_amount: Optional[float] = None
    notes: Optional[str] = None
    payload_json: Dict[str, Any] = Field(default_factory=dict)
