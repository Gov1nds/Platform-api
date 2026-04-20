"""
webhooks.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Webhook Ingestion Schema Layer

CONTRACT AUTHORITY: contract.md §4.12 (Webhook endpoints), §2.47 (Shipment),
§2.48 (Shipment_Event), §3.8 (SM-007 shipment carrier states), §3.32
(ShipmentEvent.source vocabulary), §3.59 (carrier name vocabulary).

Owned endpoints (Repo C):
  POST /api/v1/webhooks/carriers/{carrier}   — dhl | fedex | ups | maersk
  POST /api/v1/webhooks/esignature           — e-signature provider
  POST /api/v1/webhooks/erp/{system}         — sap | oracle | netsuite (Enterprise)

Invariants:
  • Every webhook endpoint performs signature verification BEFORE parsing
    the business payload.  A 401 is returned on bad/missing signature.
  • Repo C is the SOLE receiver and processor of external webhooks (§1.2,
    §6 Ownership Rules: Webhook reception → Repo C).
  • Carrier payloads are provider-specific; schemas capture known required
    fields and allow extra data in raw_payload_json (mirrored to
    shipment_event.raw_payload_json per §2.48).
  • Signature headers per carrier (§4.12):
      DHL     → X-DHL-Signature
      FedEx   → X-FedEx-Signature + X-FedEx-SigningTime
      UPS     → X-UPS-Signature
      Maersk  → X-Maersk-Signature
  • Carrier name enum per CN-10/§3.59: DHL | FedEx | UPS | Maersk | other
  • ERP webhooks: Enterprise plan only (org.billing_plan = ENTERPRISE).
  • All timestamps in ISO-8601 / TIMESTAMPTZ-compatible strings.
  • Replay protection: Repo C tracks processed webhook IDs in Redis to
    deduplicate retried deliveries within a 24-hour window.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Union
from uuid import UUID

from pydantic import Field, field_validator

from .common import (
    Carrier,
    CorrelationID,
    PGIBase,
    ShipmentEventSource,
    ShipmentState,
)


# ──────────────────────────────────────────────────────────────────────────
# Shared response — used by all webhook endpoints (§4.12)
# ──────────────────────────────────────────────────────────────────────────

class WebhookReceivedResponse(PGIBase):
    """Standard acknowledgement for all webhook endpoints.

    HTTP 200 with ``{ "received": true }`` is the contract-mandated response
    when a webhook payload is accepted and enqueued for processing.

    A synchronous 200 does NOT mean the shipment state has been updated yet —
    that happens asynchronously by the notify worker.
    """

    received: Literal[True] = True


# ──────────────────────────────────────────────────────────────────────────
# Carrier webhook — shared milestone sub-model
# ──────────────────────────────────────────────────────────────────────────

class CarrierWebhookMilestone(PGIBase):
    """A single tracking milestone event inside a carrier webhook payload.

    Maps to a shipment_event row (§2.48, APPEND-ONLY).

    milestone: carrier-provided event code or description.
    location: free-form location string from carrier (may be null).
    occurred_at: when the event occurred at the carrier level.
    source: always 'webhook' for events received via this endpoint (§3.32).
    raw_payload_json: verbatim carrier-specific fragment retained for audit.
    """

    milestone: str = Field(max_length=64)
    location: Optional[str] = Field(default=None, max_length=255)
    occurred_at: datetime
    source: ShipmentEventSource = ShipmentEventSource.WEBHOOK
    raw_payload_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# DHL webhook (§4.12 — X-DHL-Signature header)
# ──────────────────────────────────────────────────────────────────────────

class DHLWebhookPayload(PGIBase):
    """Parsed DHL tracking event webhook payload.

    DHL delivers shipment milestones via JSON events.  Known fields are typed
    explicitly; all other DHL-specific data is preserved in ``extra_json``.

    References: DHL Express Tracking API v2 + DHL Freight event webhook spec.
    """

    # DHL event ID for idempotency deduplication (stored in Redis).
    event_id: str = Field(max_length=128, description="DHL-assigned event ID.")
    shipment_tracking_number: str = Field(
        max_length=128,
        description="Tracking number that maps to shipment.tracking_number.",
    )
    timestamp: datetime = Field(
        description="ISO-8601 event timestamp from DHL.",
    )
    # Maps to Shipment.state and ShipmentEvent.milestone.
    description: str = Field(
        max_length=255,
        description="Human-readable event description from DHL.",
    )
    event_code: Optional[str] = Field(
        default=None,
        max_length=16,
        description="DHL event code (e.g. LDDP, TP, DL).",
    )
    location: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Location string as provided by DHL.",
    )
    # Inferred from DHL event code by Repo C carrier-event mapper.
    inferred_state: Optional[ShipmentState] = Field(
        default=None,
        description=(
            "Repo C infers the canonical Shipment.state from the DHL event "
            "code before writing the shipment_event row.  Not set in the "
            "raw payload — populated by the DHL integration worker."
        ),
    )
    extra_json: dict[str, Any] = Field(
        default_factory=dict,
        description="All other DHL-provided fields verbatim.",
    )


# ──────────────────────────────────────────────────────────────────────────
# FedEx webhook (§4.12 — X-FedEx-Signature + X-FedEx-SigningTime headers)
# ──────────────────────────────────────────────────────────────────────────

class FedExWebhookPayload(PGIBase):
    """Parsed FedEx tracking event webhook payload.

    FedEx wraps events in an ``events`` array.  Repo C processes each event
    individually, creating one shipment_event row per milestone (§2.48).

    References: FedEx Track API v1 webhook notification spec.
    """

    event_id: str = Field(
        max_length=128,
        description="FedEx event notification ID — used for deduplication.",
    )
    tracking_number: str = Field(
        max_length=128,
        description="Maps to shipment.tracking_number.",
    )
    service_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="FedEx service type (e.g. FEDEX_GROUND, INTERNATIONAL_PRIORITY).",
    )
    timestamp: datetime
    event_type: str = Field(
        max_length=128,
        description="FedEx event type string (e.g. OD, DL, OC).",
    )
    event_description: str = Field(max_length=255)
    location: Optional[str] = Field(default=None, max_length=255)
    inferred_state: Optional[ShipmentState] = Field(
        default=None,
        description="Populated by the FedEx integration worker, not the raw payload.",
    )
    extra_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# UPS webhook (§4.12 — X-UPS-Signature header)
# ──────────────────────────────────────────────────────────────────────────

class UPSWebhookPayload(PGIBase):
    """Parsed UPS tracking event webhook payload.

    References: UPS Quantum View Notify + UPS Developer Kit webhook events.
    """

    event_id: str = Field(
        max_length=128,
        description="UPS event reference for deduplication.",
    )
    tracking_number: str = Field(max_length=128)
    timestamp: datetime
    activity_status: str = Field(
        max_length=64,
        description="UPS activity status code (e.g. I, D, P).",
    )
    activity_description: str = Field(max_length=255)
    location: Optional[str] = Field(default=None, max_length=255)
    inferred_state: Optional[ShipmentState] = Field(
        default=None,
        description="Populated by UPS integration worker.",
    )
    extra_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Maersk webhook (§4.12 — X-Maersk-Signature header)
# ──────────────────────────────────────────────────────────────────────────

class MaerskWebhookPayload(PGIBase):
    """Parsed Maersk ocean freight tracking event webhook payload.

    Maersk delivers container-level events; Repo C maps these to
    ShipmentState transitions for CUSTOMS_HOLD → CUSTOMS_CLEARED and
    IN_TRANSIT → DELIVERED (§3.8).

    References: Maersk Shipping API / Track & Trace webhook v3.
    """

    event_id: str = Field(max_length=128)
    bill_of_lading: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Maersk B/L number.",
    )
    container_number: Optional[str] = Field(
        default=None,
        max_length=64,
    )
    tracking_number: str = Field(
        max_length=128,
        description="Maps to shipment.tracking_number.",
    )
    event_code: str = Field(
        max_length=32,
        description="Maersk event type code (e.g. ARRI, DEPA, GTIN, GTOUT).",
    )
    event_description: str = Field(max_length=255)
    timestamp: datetime
    location_name: Optional[str] = Field(default=None, max_length=255)
    voyage_number: Optional[str] = Field(default=None, max_length=32)
    vessel_name: Optional[str] = Field(default=None, max_length=128)
    inferred_state: Optional[ShipmentState] = Field(
        default=None,
        description="Populated by Maersk integration worker.",
    )
    extra_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Carrier webhook union — for route handler type hints
# ──────────────────────────────────────────────────────────────────────────

CarrierWebhookPayload = Union[
    DHLWebhookPayload,
    FedExWebhookPayload,
    UPSWebhookPayload,
    MaerskWebhookPayload,
]


# ──────────────────────────────────────────────────────────────────────────
# E-signature webhook (§4.12)
# ──────────────────────────────────────────────────────────────────────────

class ESignatureWebhookPayload(PGIBase):
    """Parsed e-signature provider webhook payload.

    Used for NDA completion events between buyer organizations and vendors
    (requirements.yaml — e-signature provider workflow).

    Repo C uses this to:
    1. Mark the associated document as signed.
    2. Advance the vendor profile claim workflow if pending.
    3. Write an Event_Audit_Log entry.

    The actual provider (DocuSign, Adobe Sign, etc.) is configured via
    environment variable — the schema covers the common abstraction.
    """

    event_id: str = Field(
        max_length=128,
        description="Provider-assigned event ID for deduplication.",
    )
    event_type: str = Field(
        max_length=64,
        description=(
            "Provider event type; expected values include 'envelope_completed', "
            "'envelope_declined', 'envelope_voided'."
        ),
    )
    envelope_id: str = Field(
        max_length=128,
        description="Provider envelope/document ID.",
    )
    # Repo C maps this to a document or vendor_profile_claim.
    reference_entity_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "PGI entity type this envelope references "
            "(e.g. 'vendor_profile_claim', 'rfq', 'po')."
        ),
    )
    reference_entity_id: Optional[UUID] = Field(
        default=None,
        description="PGI entity ID this envelope belongs to.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When all signatories completed — non-null for completion events.",
    )
    signatories: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of signatory records from the provider.",
    )
    status: str = Field(
        max_length=32,
        description=(
            "Provider envelope status string "
            "(e.g. 'completed', 'declined', 'voided')."
        ),
    )
    extra_json: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# ERP webhooks (§4.12 — Enterprise only: sap | oracle | netsuite)
# ──────────────────────────────────────────────────────────────────────────

class ERPWebhookPayload(PGIBase):
    """Generic ERP event webhook payload (base for all ERP systems).

    All ERP webhook handlers (SAP, Oracle, NetSuite) accept a signed
    request body.  The ``system`` field discriminates between providers.
    Only organizations with billing_plan = ENTERPRISE may register ERP
    webhooks.

    Repo C uses ERP events to:
    - Receive payment confirmations (Invoice → PAID via erp source, §3.85).
    - Receive PO acknowledgements from ERP-driven vendors.
    - Sync goods receipt confirmations from WMS/ERP.
    """

    system: str = Field(
        max_length=16,
        description="ERP system identifier: sap | oracle | netsuite.",
    )
    event_id: str = Field(
        max_length=128,
        description="ERP-assigned event ID for deduplication.",
    )
    event_type: str = Field(
        max_length=128,
        description=(
            "ERP domain event type "
            "(e.g. 'payment_cleared', 'po_acknowledged', 'gr_posted')."
        ),
    )
    entity_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="PGI entity type affected (e.g. 'invoice', 'purchase_order').",
    )
    entity_id: Optional[UUID] = Field(
        default=None,
        description="PGI entity UUID affected by this ERP event.",
    )
    erp_document_number: Optional[str] = Field(
        default=None,
        max_length=128,
        description="ERP document number (e.g. SAP invoice number, NetSuite SO number).",
    )
    occurred_at: datetime
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Full raw ERP event payload (verbatim, for audit).",
    )


class SAPWebhookPayload(ERPWebhookPayload):
    """SAP-specific ERP webhook payload.

    system is always 'sap'.
    Signature header: SAP BTP webhook signature mechanism (HMAC-SHA256).
    """

    system: Literal["sap"] = "sap"
    sap_client: Optional[str] = Field(
        default=None,
        max_length=16,
        description="SAP client identifier (mandant).",
    )
    sap_system_id: Optional[str] = Field(
        default=None,
        max_length=8,
        description="SAP system ID (SID).",
    )


class OracleWebhookPayload(ERPWebhookPayload):
    """Oracle ERP Cloud-specific webhook payload.

    system is always 'oracle'.
    Signature header: Oracle IDCS OAuth / webhook signing key.
    """

    system: Literal["oracle"] = "oracle"
    oracle_business_unit_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Oracle Business Unit ID.",
    )
    oracle_transaction_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Oracle transaction type code (e.g. PO, APINV, GR).",
    )


class NetSuiteWebhookPayload(ERPWebhookPayload):
    """NetSuite SuiteCloud-specific webhook payload.

    system is always 'netsuite'.
    Signature header: NetSuite OAuth 1.0 NLAuth signature.
    """

    system: Literal["netsuite"] = "netsuite"
    netsuite_account_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="NetSuite account ID.",
    )
    netsuite_record_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="NetSuite record type (e.g. invoice, purchaseorder, itemreceipt).",
    )
    netsuite_record_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="NetSuite internal record ID.",
    )


# ──────────────────────────────────────────────────────────────────────────
# ERP webhook union — for route handler type hints
# ──────────────────────────────────────────────────────────────────────────

ERPWebhookPayloadUnion = Union[
    SAPWebhookPayload,
    OracleWebhookPayload,
    NetSuiteWebhookPayload,
]