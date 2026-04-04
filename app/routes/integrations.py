"""External integrations, document lineage, and observability routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.integration import (
    AlternateApprovalRequest,
    AlternatePartApprovalSchema,
    CatalogSyncRequest,
    ComplianceRefreshRequest,
    DocumentAssetSchema,
    DocumentUploadRequest,
    EmailIngestRequest,
    EmailIngestMessageSchema,
    ExternalFeedSnapshotSchema,
    GoodsReceiptReconciliationRequest,
    GoodsReceiptReconciliationSchema,
    IntegrationEventSchema,
    TrackingNumberHistorySchema,
    VendorComplianceRefreshSchema,
    VendorContactSchema,
    VendorOperationalProfileSchema,
    WebhookRequest,
)
from app.services import integration_service
from app.services.storage_service import save_bytes
from app.utils.dependencies import require_roles

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _verify_integration_secret(x_integration_secret: Optional[str] = Header(None, alias="X-Integration-Secret")) -> None:
    if not settings.INTEGRATION_WEBHOOK_SECRET:
        return
    if not x_integration_secret or x_integration_secret.strip() != settings.INTEGRATION_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid integration secret")


@router.get("/health")
def health():
    return {
        "status": "ok",
        "object_storage_provider": settings.OBJECT_STORAGE_PROVIDER,
        "webhook_secret_configured": bool(settings.INTEGRATION_WEBHOOK_SECRET),
    }


@router.post("/feeds/vendor-catalogs", response_model=List[ExternalFeedSnapshotSchema])
def sync_vendor_catalog_feed(
    body: CatalogSyncRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    rows = integration_service.ingest_vendor_catalog(
        db,
        source_name=body.source_name,
        feed_type=body.feed_type,
        items=body.items,
        vendor_id=body.vendor_id,
        source_url=body.source_url,
    )
    db.commit()
    return rows


@router.post("/feeds/vendor-compliance", response_model=VendorComplianceRefreshSchema)
def refresh_vendor_compliance_feed(
    body: ComplianceRefreshRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    row = integration_service.refresh_vendor_compliance(
        db,
        vendor_id=body.vendor_id,
        certification_name=body.certification_name,
        certification_id=body.certification_id,
        issued_by=body.issued_by,
        issued_at=body.issued_at,
        expires_at=body.expires_at,
        status=body.status,
        source_url=body.source_url,
        payload_json=body.payload_json,
    )
    db.commit()
    return row


@router.post("/feeds/vendor-operational-profile", response_model=VendorOperationalProfileSchema)
def upsert_vendor_operational_profile(
    body: Dict[str, Any],
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    vendor_id = body.get("vendor_id")
    if not vendor_id:
        raise HTTPException(status_code=400, detail="vendor_id is required")
    profile = integration_service.upsert_vendor_operational_profile(
        db,
        vendor_id=vendor_id,
        default_currency=body.get("default_currency"),
        default_incoterms=body.get("default_incoterms"),
        freight_terms=body.get("freight_terms"),
        payment_terms=body.get("payment_terms"),
        default_quote_valid_days=body.get("default_quote_valid_days"),
        sample_orders_supported=body.get("sample_orders_supported"),
        quality_rating=body.get("quality_rating"),
        logistics_capability=body.get("logistics_capability"),
        capacity_notes=body.get("capacity_notes"),
        default_region=body.get("default_region"),
        regions_served=body.get("regions_served"),
        moq_by_process=body.get("moq_by_process"),
        moq_by_part=body.get("moq_by_part"),
        lead_time_by_process=body.get("lead_time_by_process"),
        quote_validity_policy=body.get("quote_validity_policy"),
        metadata=body.get("metadata"),
    )
    db.commit()
    return profile


@router.post("/webhooks/vendor-quotes", response_model=IntegrationEventSchema)
def quote_webhook(
    body: WebhookRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    event = integration_service.ingest_quote_webhook(db, body.model_dump(mode="json"))
    db.commit()
    return event


@router.post("/webhooks/carriers", response_model=IntegrationEventSchema)
def carrier_webhook(
    body: WebhookRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    event = integration_service.ingest_carrier_webhook(db, body.model_dump(mode="json"))
    tracking = integration_service.maybe_link_tracking_update(db, body.model_dump(mode="json"))
    if tracking:
        event.metadata_ = {**(event.metadata_ or {}), "tracking_history_id": str(tracking.id)}
    db.commit()
    return event


@router.post("/webhooks/erp", response_model=IntegrationEventSchema)
def erp_sync_webhook(
    body: WebhookRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    event = integration_service.record_event(
        db,
        event_type=body.event_type or "sync_completed",
        source_system=body.source_system or "erp",
        target_system=body.target_system or "platform_api",
        status=body.status,
        severity=body.severity,
        external_reference=body.external_reference,
        request_path=body.request_path,
        payload_json=body.payload_json,
        error_text=body.error_text,
        metadata={"integration_kind": "erp_sync"},
    )
    db.commit()
    return event


@router.post("/email/inbound", response_model=EmailIngestMessageSchema)
def inbound_email(
    body: EmailIngestRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    row = integration_service.ingest_email_message(db, body.model_dump(mode="json"))
    db.commit()
    return row


@router.post("/documents/upload", response_model=DocumentAssetSchema)
async def upload_document_asset(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    source_id: str = Form(...),
    project_id: Optional[str] = Form(None),
    bom_id: Optional[str] = Form(None),
    rfq_batch_id: Optional[str] = Form(None),
    vendor_id: Optional[str] = Form(None),
    purchase_order_id: Optional[str] = Form(None),
    shipment_id: Optional[str] = Form(None),
    invoice_id: Optional[str] = Form(None),
    asset_kind: Optional[str] = Form("generic"),
    revision_label: Optional[str] = Form(None),
    is_current: bool = Form(True),
    user: User = Depends(require_roles("admin", "manager", "buyer", "sourcing")),
    db: Session = Depends(get_db),
):
    file_bytes = await file.read()
    stored = save_bytes(file_bytes, file.filename or "document.bin", scope=source_type)
    asset = integration_service.register_document_asset(
        db,
        source_type=source_type,
        source_id=source_id,
        storage_provider=stored.provider,
        storage_key=stored.storage_key,
        file_name=file.filename or source_id,
        mime_type=file.content_type,
        file_size_bytes=stored.file_size_bytes,
        sha256=stored.sha256,
        project_id=project_id,
        bom_id=bom_id,
        rfq_batch_id=rfq_batch_id,
        vendor_id=vendor_id,
        purchase_order_id=purchase_order_id,
        shipment_id=shipment_id,
        invoice_id=invoice_id,
        uploaded_by_user_id=user.id,
        asset_kind=asset_kind or "generic",
        revision_label=revision_label,
        is_current=is_current,
        metadata={"upload_filename": file.filename, "upload_content_type": file.content_type},
        public_url=stored.public_url,
    )
    db.commit()
    return asset


@router.post("/alternates/approve", response_model=AlternatePartApprovalSchema)
def approve_alternate_part(
    body: AlternateApprovalRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    row = integration_service.approve_alternate_part(db, body.model_dump(mode="json"))
    db.commit()
    return row


@router.post("/reconciliation/goods-receipts", response_model=GoodsReceiptReconciliationSchema)
def reconcile_goods_receipt(
    body: GoodsReceiptReconciliationRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    row = integration_service.reconcile_goods_receipt(db, body.model_dump(mode="json"))
    db.commit()
    return row


@router.post("/observability/events", response_model=IntegrationEventSchema)
def record_observability_event(
    body: WebhookRequest,
    _: None = Depends(_verify_integration_secret),
    db: Session = Depends(get_db),
):
    event = integration_service.record_event(
        db,
        event_type=body.event_type,
        source_system=body.source_system,
        target_system=body.target_system,
        status=body.status,
        severity=body.severity,
        external_reference=body.external_reference,
        request_path=body.request_path,
        payload_json=body.payload_json,
        error_text=body.error_text,
    )
    db.commit()
    return event
