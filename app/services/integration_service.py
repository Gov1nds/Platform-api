"""Integration data capture service.

Keeps the platform's external data sources, document lineage, and operational
observability in a single auditable surface.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.integration_assets import (
    AlternatePartApproval,
    DocumentAsset,
    EmailIngestMessage,
    ExternalFeedSnapshot,
    GoodsReceiptReconciliation,
    IntegrationEvent,
    TrackingNumberHistory,
    VendorComplianceRefresh,
    VendorContact,
    VendorOperationalProfile,
)
from app.models.project import Project
from app.models.vendor import Vendor
from app.models.tracking import Shipment, GoodsReceipt, Invoice

logger = logging.getLogger("integration_service")


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def record_event(
    db: Session,
    *,
    event_type: str,
    source_system: str,
    target_system: Optional[str] = None,
    status: str = "received",
    severity: str = "info",
    correlation_id: Optional[str] = None,
    external_reference: Optional[str] = None,
    request_method: Optional[str] = None,
    request_path: Optional[str] = None,
    payload_json: Optional[Dict[str, Any]] = None,
    error_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    resolved_at: Optional[datetime] = None,
) -> IntegrationEvent:
    event = IntegrationEvent(
        event_type=event_type,
        source_system=source_system,
        target_system=target_system,
        status=status,
        severity=severity,
        correlation_id=correlation_id,
        external_reference=external_reference,
        request_method=request_method,
        request_path=request_path,
        payload_json=payload_json or {},
        error_text=error_text,
        metadata_=metadata or {},
        resolved_at=resolved_at,
    )
    db.add(event)
    db.flush()
    return event


def register_document_asset(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    storage_provider: str,
    storage_key: str,
    file_name: str,
    mime_type: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    sha256: Optional[str] = None,
    project_id: Optional[str] = None,
    bom_id: Optional[str] = None,
    rfq_batch_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    invoice_id: Optional[str] = None,
    uploaded_by_user_id: Optional[str] = None,
    asset_kind: str = "generic",
    revision_label: Optional[str] = None,
    is_current: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    public_url: Optional[str] = None,
) -> DocumentAsset:
    asset = DocumentAsset(
        source_type=source_type,
        source_id=_safe_str(source_id),
        project_id=project_id,
        bom_id=bom_id,
        rfq_batch_id=rfq_batch_id,
        vendor_id=vendor_id,
        purchase_order_id=purchase_order_id,
        shipment_id=shipment_id,
        invoice_id=invoice_id,
        storage_provider=storage_provider,
        storage_key=storage_key,
        public_url=public_url,
        file_name=file_name,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        sha256=sha256,
        asset_kind=asset_kind,
        revision_label=revision_label,
        is_current=is_current,
        uploaded_by_user_id=uploaded_by_user_id,
        metadata_=metadata or {},
    )
    db.add(asset)
    db.flush()
    return asset


def ingest_vendor_catalog(
    db: Session,
    *,
    source_name: str,
    feed_type: str,
    items: list[Dict[str, Any]],
    vendor_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> list[ExternalFeedSnapshot]:
    rows: list[ExternalFeedSnapshot] = []
    for item in items:
        row = ExternalFeedSnapshot(
            feed_type=feed_type,
            source_name=source_name,
            vendor_id=vendor_id,
            external_id=_safe_str(item.get("external_id") or item.get("id") or item.get("sku")),
            external_part_number=item.get("external_part_number") or item.get("mpn") or item.get("part_number"),
            canonical_part_key=item.get("canonical_part_key") or item.get("normalized_key"),
            part_name=item.get("part_name") or item.get("name"),
            description=item.get("description"),
            source_currency=_safe_str(item.get("currency"), "USD") or "USD",
            unit_price=item.get("unit_price") or item.get("price"),
            moq=item.get("moq") or item.get("minimum_order_quantity"),
            lead_time_days=item.get("lead_time_days") or item.get("lead_time"),
            incoterms=item.get("incoterms"),
            freight_terms=item.get("freight_terms"),
            tax_region=item.get("tax_region"),
            duty_region=item.get("duty_region"),
            quote_valid_until=item.get("quote_valid_until"),
            availability_status=item.get("availability_status"),
            compliance_status=item.get("compliance_status"),
            region=item.get("region"),
            country=item.get("country"),
            source_url=item.get("source_url") or source_url,
            source_payload=item,
            normalized_payload=item.get("normalized_payload") or {},
            expires_at=item.get("expires_at"),
            metadata_=item.get("metadata") or {},
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def refresh_vendor_compliance(
    db: Session,
    *,
    vendor_id: str,
    certification_name: str,
    certification_id: Optional[str] = None,
    issued_by: Optional[str] = None,
    issued_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    status: str = "active",
    source_url: Optional[str] = None,
    payload_json: Optional[Dict[str, Any]] = None,
) -> VendorComplianceRefresh:
    row = VendorComplianceRefresh(
        vendor_id=vendor_id,
        certification_name=certification_name,
        certification_id=certification_id,
        issued_by=issued_by,
        issued_at=issued_at,
        expires_at=expires_at,
        status=status,
        source_url=source_url,
        payload_json=payload_json or {},
    )
    db.add(row)
    db.flush()
    return row


def upsert_vendor_operational_profile(
    db: Session,
    *,
    vendor_id: str,
    default_currency: Optional[str] = None,
    default_incoterms: Optional[str] = None,
    freight_terms: Optional[str] = None,
    payment_terms: Optional[str] = None,
    default_quote_valid_days: Optional[int] = None,
    sample_orders_supported: Optional[bool] = None,
    quality_rating: Optional[float] = None,
    logistics_capability: Optional[float] = None,
    capacity_notes: Optional[str] = None,
    default_region: Optional[str] = None,
    regions_served: Optional[list[str]] = None,
    moq_by_process: Optional[Dict[str, Any]] = None,
    moq_by_part: Optional[Dict[str, Any]] = None,
    lead_time_by_process: Optional[Dict[str, Any]] = None,
    quote_validity_policy: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> VendorOperationalProfile:
    profile = db.query(VendorOperationalProfile).filter(VendorOperationalProfile.vendor_id == vendor_id).first()
    if not profile:
        profile = VendorOperationalProfile(vendor_id=vendor_id)
        db.add(profile)

    if default_currency is not None:
        profile.default_currency = default_currency
    if default_incoterms is not None:
        profile.default_incoterms = default_incoterms
    if freight_terms is not None:
        profile.freight_terms = freight_terms
    if payment_terms is not None:
        profile.payment_terms = payment_terms
    if default_quote_valid_days is not None:
        profile.default_quote_valid_days = default_quote_valid_days
    if sample_orders_supported is not None:
        profile.sample_orders_supported = bool(sample_orders_supported)
    if quality_rating is not None:
        profile.quality_rating = quality_rating
    if logistics_capability is not None:
        profile.logistics_capability = logistics_capability
    if capacity_notes is not None:
        profile.capacity_notes = capacity_notes
    if default_region is not None:
        profile.default_region = default_region
    if regions_served is not None:
        profile.regions_served = regions_served
    if moq_by_process is not None:
        profile.moq_by_process = moq_by_process
    if moq_by_part is not None:
        profile.moq_by_part = moq_by_part
    if lead_time_by_process is not None:
        profile.lead_time_by_process = lead_time_by_process
    if quote_validity_policy is not None:
        profile.quote_validity_policy = quote_validity_policy
    if metadata is not None:
        profile.metadata_ = metadata
    db.flush()
    return profile


def ingest_email_message(db: Session, payload: Dict[str, Any]) -> EmailIngestMessage:
    row = EmailIngestMessage(
        message_id=_safe_str(payload.get("message_id") or payload.get("id") or payload.get("messageId")),
        thread_token=payload.get("thread_token") or payload.get("threadToken"),
        from_email=payload.get("from_email") or payload.get("from"),
        to_email=payload.get("to_email") or payload.get("to"),
        subject=payload.get("subject"),
        body_text=payload.get("body_text") or payload.get("body"),
        raw_headers_json=payload.get("raw_headers_json") or payload.get("headers") or {},
        attachment_count=int(payload.get("attachment_count") or 0),
        parsed_status=payload.get("parsed_status") or "received",
        parse_summary=payload.get("parse_summary") or {},
        rfq_batch_id=payload.get("rfq_batch_id"),
        project_id=payload.get("project_id"),
        vendor_id=payload.get("vendor_id"),
        payload_json=payload,
    )
    db.add(row)
    db.flush()
    return row


def ingest_quote_webhook(db: Session, payload: Dict[str, Any], *, source_system: str = "vendor_webhook") -> IntegrationEvent:
    return record_event(
        db,
        event_type="webhook_received",
        source_system=source_system,
        target_system="sourcing",
        status=payload.get("status", "received"),
        severity=payload.get("severity", "info"),
        correlation_id=payload.get("correlation_id"),
        external_reference=payload.get("quote_number") or payload.get("external_reference"),
        request_path=payload.get("request_path"),
        payload_json=payload,
        metadata={"webhook_kind": "quote_submission"},
    )


def ingest_carrier_webhook(db: Session, payload: Dict[str, Any], *, source_system: str = "carrier_webhook") -> IntegrationEvent:
    return record_event(
        db,
        event_type="webhook_received",
        source_system=source_system,
        target_system="ops",
        status=payload.get("status", "received"),
        severity=payload.get("severity", "info"),
        correlation_id=payload.get("tracking_number") or payload.get("correlation_id"),
        external_reference=payload.get("tracking_number"),
        request_path=payload.get("request_path"),
        payload_json=payload,
        metadata={"webhook_kind": "carrier_tracking"},
    )


def reconcile_goods_receipt(db: Session, payload: Dict[str, Any]) -> GoodsReceiptReconciliation:
    row = GoodsReceiptReconciliation(
        purchase_order_id=payload.get("purchase_order_id"),
        shipment_id=payload.get("shipment_id"),
        goods_receipt_id=payload.get("goods_receipt_id"),
        invoice_id=payload.get("invoice_id"),
        reconciliation_status=payload.get("reconciliation_status") or "pending",
        matched_quantity=payload.get("matched_quantity"),
        matched_amount=payload.get("matched_amount"),
        variance_amount=payload.get("variance_amount"),
        notes=payload.get("notes"),
        payload_json=payload,
        matched_at=payload.get("matched_at"),
        resolved_by_user_id=payload.get("resolved_by_user_id"),
    )
    db.add(row)
    db.flush()
    return row


def approve_alternate_part(db: Session, payload: Dict[str, Any]) -> AlternatePartApproval:
    row = AlternatePartApproval(
        bom_id=payload.get("bom_id"),
        bom_part_id=payload.get("bom_part_id"),
        alternate_part_key=payload.get("alternate_part_key"),
        alternate_mpn=payload.get("alternate_mpn"),
        alternate_manufacturer=payload.get("alternate_manufacturer"),
        approval_status=payload.get("approval_status") or "pending",
        approval_reason=payload.get("approval_reason"),
        approved_by_user_id=payload.get("approved_by_user_id"),
        approved_at=payload.get("approved_at"),
        effective_at=payload.get("effective_at"),
        expires_at=payload.get("expires_at"),
        metadata_=payload.get("metadata") or {},
    )
    db.add(row)
    db.flush()
    return row


def record_tracking_number_history(
    db: Session,
    *,
    shipment_id: str,
    purchase_order_id: Optional[str],
    tracking_number: str,
    carrier_name: Optional[str] = None,
    carrier_code: Optional[str] = None,
    tracking_number_source: Optional[str] = None,
    status: str = "active",
    effective_from: Optional[datetime] = None,
    effective_to: Optional[datetime] = None,
    payload_json: Optional[Dict[str, Any]] = None,
) -> TrackingNumberHistory:
    row = TrackingNumberHistory(
        shipment_id=shipment_id,
        purchase_order_id=purchase_order_id,
        carrier_name=carrier_name,
        carrier_code=carrier_code,
        tracking_number=tracking_number,
        tracking_number_source=tracking_number_source,
        status=status,
        effective_from=effective_from or datetime.utcnow(),
        effective_to=effective_to,
        payload_json=payload_json or {},
    )
    db.add(row)
    db.flush()
    return row


def _shipment_from_reference(db: Session, shipment_id: Optional[str] = None, tracking_number: Optional[str] = None):
    query = db.query(Shipment)
    if shipment_id:
        shipment = query.filter(Shipment.id == shipment_id).first()
        if shipment:
            return shipment
    if tracking_number:
        return query.filter(Shipment.tracking_number == tracking_number).first()
    return None


def maybe_link_tracking_update(db: Session, payload: Dict[str, Any]) -> Optional[TrackingNumberHistory]:
    shipment = _shipment_from_reference(
        db,
        shipment_id=payload.get("shipment_id"),
        tracking_number=payload.get("tracking_number"),
    )
    if not shipment:
        return None
    return record_tracking_number_history(
        db,
        shipment_id=shipment.id,
        purchase_order_id=shipment.purchase_order_id,
        tracking_number=_safe_str(payload.get("tracking_number") or shipment.tracking_number),
        carrier_name=payload.get("carrier_name") or shipment.carrier_name,
        carrier_code=payload.get("carrier_code") or shipment.carrier_code,
        tracking_number_source=payload.get("tracking_number_source") or payload.get("source_system"),
        status=payload.get("status") or shipment.status or "active",
        payload_json=payload,
    )


def maybe_link_goods_reconciliation(db: Session, payload: Dict[str, Any]) -> Optional[GoodsReceiptReconciliation]:
    if not payload.get("goods_receipt_id"):
        return None
    return reconcile_goods_receipt(db, payload)


def maybe_record_api_error(
    db: Session,
    *,
    request_method: Optional[str],
    request_path: Optional[str],
    error_text: str,
    payload_json: Optional[Dict[str, Any]] = None,
    severity: str = "error",
    source_system: str = "platform_api",
) -> IntegrationEvent:
    return record_event(
        db,
        event_type="api_error",
        source_system=source_system,
        target_system="platform_api",
        status="failed",
        severity=severity,
        request_method=request_method,
        request_path=request_path,
        payload_json=payload_json or {},
        error_text=error_text,
    )
