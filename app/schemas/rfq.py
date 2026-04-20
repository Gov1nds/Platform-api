"""
rfq.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — RFQ Schema Layer

CONTRACT AUTHORITY: contract.md §2.36–2.38 (RFQ, RFQ_Line, RFQ_Vendor_Invite),
§3.5 (SM-004 RFQ.status), §4.6 (RFQ & Quote endpoints).

CN-4:  selected_vendors and selected_lines MUST NOT exist as array columns
       on rfq — data lives in rfq_line and rfq_vendor_invite tables.
CN-5:  RFQ status vocabulary is uppercase 7-state (SM-004).
CN-13: Minimum 3 vendors per RFQ line before DRAFT → SENT transition.

Invariants:
  • terms_snapshot is IMMUTABLE after RFQ.status = SENT (app-enforced).
  • deadline must be > created_at; Repo C rejects deadline < 48h from now.
  • idempotency_key: UNIQUE; Idempotency-Key header required on POST /rfqs.
  • All RFQ.status transitions exclusively owned by Repo C.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    DataFreshnessEnvelope,
    IdempotencyKey,
    PGIBase,
    RFQInviteDeliveryMethod,
    RFQSendChannel,
    RFQStatus,
    TIMESTAMPTZ,
)


# ──────────────────────────────────────────────────────────────────────────
# RFQ entity (contract §2.36)
# ──────────────────────────────────────────────────────────────────────────

class RFQResponse(PGIBase):
    """Full RFQ entity.

    CN-4: selected_vendors and selected_lines are NOT columns — use the
    rfq_line and rfq_vendor_invite sub-resources instead.
    terms_snapshot: IMMUTABLE after SENT.
    """

    rfq_id: UUID
    project_id: UUID
    created_by: UUID
    deadline: datetime
    status: RFQStatus
    vendor_selection_reason_json: dict[str, Any] = Field(default_factory=dict)
    terms_snapshot: dict[str, Any] = Field(
        description="IMMUTABLE after SENT: incoterms, payment_terms, nda_required, etc.",
    )
    send_channel: RFQSendChannel
    sent_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Expanded sub-resources (populated on GET /rfqs/{id})
    lines: Optional[list["RFQLineSchema"]] = None
    invites: Optional[list["RFQVendorInviteSchema"]] = None
    quotes: Optional[list[dict[str, Any]]] = None


class BuyerFacingRFQResponse(RFQResponse):
    """Full buyer-side RFQ projection including invite and comparison context."""


class VendorFacingRFQResponse(PGIBase):
    """Vendor-safe RFQ projection.

    Excludes vendor_selection_reason_json, the complete invite list, and
    competing vendor quotes.
    """

    rfq_id: UUID
    project_id: UUID
    deadline: datetime
    status: RFQStatus
    terms_snapshot: dict[str, Any]
    send_channel: RFQSendChannel
    sent_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    lines: Optional[list["RFQLineSchema"]] = None


class RFQSummaryResponse(PGIBase):
    """Compact RFQ for list views."""

    rfq_id: UUID
    project_id: UUID
    status: RFQStatus
    deadline: datetime
    send_channel: RFQSendChannel
    vendor_count: Optional[int] = None
    line_count: Optional[int] = None
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/rfqs (contract §4.6)
# ──────────────────────────────────────────────────────────────────────────

class RFQLineQuantityRequest(PGIBase):
    """Per-line quantity and date override for an RFQ creation request."""

    bom_line_id: UUID
    quantity: Decimal = Field(gt=Decimal("0"))
    required_by_date: Optional[date] = Field(
        default=None, description="ISO date YYYY-MM-DD."
    )


class RFQTermsSnapshot(PGIBase):
    """Terms that are captured immutably when the RFQ is sent.

    Stored as JSONB in the rfq.terms_snapshot column; this model
    defines the canonical shape of that blob.
    """

    incoterms: str = Field(max_length=16)
    payment_terms: str = Field(max_length=128)
    nda_required: bool = False


class RFQCreateRequest(PGIBase):
    """POST /api/v1/rfqs — create and dispatch an RFQ.

    Idempotency-Key header required (contract §4.6).
    Errors: 422 fewer_than_3_vendors (CN-13), 422 deadline_too_soon (<48h),
            402 plan_quota_exceeded.
    """

    project_id: UUID
    selected_vendor_ids: list[UUID] = Field(
        min_length=1,
        description=(
            "Vendor IDs to invite. Repo C validates >= 3 per line (CN-13). "
            "Stored in rfq_vendor_invite, NOT in rfq.selected_vendors column."
        ),
    )
    selected_bom_line_ids: list[UUID] = Field(
        min_length=1,
        description=(
            "BOM line IDs to include. Stored in rfq_line, "
            "NOT in rfq.selected_lines column (CN-4)."
        ),
    )
    line_quantities: list[RFQLineQuantityRequest] = Field(
        min_length=1,
        description="Per-line quantity and delivery date overrides.",
    )
    deadline: TIMESTAMPTZ = Field(description="RFQ response deadline (must be >= 48h from now).")
    terms_snapshot: RFQTermsSnapshot
    attachments: list[UUID] = Field(
        default_factory=list,
        description="Document IDs to attach to the RFQ (from the Document vault).",
    )
    vendor_selection_reason_json: dict[str, Any] = Field(default_factory=dict)
    send_channel: RFQSendChannel = Field(default=RFQSendChannel.MULTI)


class RFQCreateResponse(PGIBase):
    """Response after successfully creating and dispatching an RFQ.

    status: always 'SENT' on success (Repo C sends immediately after creating).
    chat_thread_ids: one Quote chat thread per invited vendor.
    """

    rfq_id: UUID
    status: RFQStatus
    chat_thread_ids: list[UUID]
    data_freshness: Optional[DataFreshnessEnvelope] = Field(
        default=None,
        description="Freshness of vendor scores used to pre-select vendors.",
    )


# ──────────────────────────────────────────────────────────────────────────
# RFQ_Line (contract §2.37)
# ──────────────────────────────────────────────────────────────────────────

class RFQLineSchema(PGIBase):
    """A single BOM line included in an RFQ.

    UNIQUE constraint: (rfq_id, bom_line_id).
    quantity_requested > 0 enforced by CHECK.
    """

    rfq_line_id: UUID
    rfq_id: UUID
    bom_line_id: UUID
    quantity_requested: Decimal = Field(gt=Decimal("0"))
    required_by_date: Optional[date] = Field(
        default=None, description="ISO date YYYY-MM-DD."
    )

    # Denormalized
    normalized_name: Optional[str] = None
    category: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# RFQ_Vendor_Invite (contract §2.38)
# ──────────────────────────────────────────────────────────────────────────

class RFQVendorInviteSchema(PGIBase):
    """Tracks an individual vendor's invitation to respond to an RFQ.

    UNIQUE constraint: (rfq_id, vendor_id).
    unique_token: used in email link for vendors responding without portal auth.
    delivery_method: portal | email | api.
    """

    invite_id: UUID
    rfq_id: UUID
    vendor_id: UUID
    vendor_name: Optional[str] = None
    delivery_method: RFQInviteDeliveryMethod
    unique_token: str = Field(max_length=128)
    sent_at: Optional[datetime] = None
    viewed_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────
# GET /api/v1/rfqs/{id}
# ──────────────────────────────────────────────────────────────────────────

class RFQDetailResponse(PGIBase):
    """Expanded RFQ with all sub-resources."""

    rfq: RFQResponse
    lines: list[RFQLineSchema]
    invites: list[RFQVendorInviteSchema]
    quotes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Quote summaries for each vendor response received so far.",
    )


# Forward reference resolution
RFQResponse.model_rebuild()
