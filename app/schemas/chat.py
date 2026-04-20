"""
chat.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Chat, Negotiation & Document Vault Schema Layer

CONTRACT AUTHORITY: contract.md §2.59–2.63 (ChatThread, ThreadParticipant,
ChatMessage, OfferEvent, Document), §3.15 (SM-012 ChatThread.status),
§4.8 (Chat endpoints), CN-7 (uppercase thread status), CN-8 (system sender).

Invariants:
  • CN-7: ChatThread.status uppercase: OPEN | RESOLVED | ARCHIVED.
  • CN-8: ChatMessage.sender_type includes 'system' for automated messages.
  • thread_type='quote' requires non-null quote_id (CHECK in DB).
  • thread_type='order' requires non-null order_id (CHECK in DB).
  • Thread ACL: enforced by thread_type + FK references (§2.59).
  • Files shared in chat live in document table; chat holds references only.
  • Offer_Event.accepted mirrors to Quote_Revision (never duplicated).
  • OfferEvent.resulting_quote_revision_id: set when offer is accepted.
  • Document.virus_scan_status must be 'CLEAN' before use (Repo C enforces).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    ChatMessageType,
    ChatMessageSenderType,
    ChatThreadStatus,
    ChatThreadType,
    DocumentEntityType,
    ChatMessageDeliveryStatus,
    OfferEventType,
    PGIBase,
    PurchaseOrderStatus,
    ThreadParticipantType,
    DocumentVirusScanStatus,
)


# ──────────────────────────────────────────────────────────────────────────
# Chat_Thread (contract §2.59)
# ──────────────────────────────────────────────────────────────────────────

class ChatThreadSchema(PGIBase):
    """A chat thread scoped to a quote, order, or general vendor relationship.

    DB CHECK: thread_type='quote' → quote_id IS NOT NULL.
    DB CHECK: thread_type='order' → order_id IS NOT NULL.
    status: OPEN | RESOLVED | ARCHIVED (CN-7: uppercase, SM-012).
    """

    thread_id: UUID
    thread_type: ChatThreadType
    quote_id: Optional[UUID] = None
    rfq_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    vendor_id: UUID
    buyer_user_id: UUID
    subject: Optional[str] = Field(default=None, max_length=255)
    status: ChatThreadStatus
    created_at: datetime
    last_message_at: datetime

    # Populated on list views
    last_message: Optional["ChatMessageSchema"] = None
    unread_count: Optional[int] = None

    @model_validator(mode="after")
    def validate_thread_type_fks(self) -> "ChatThreadSchema":
        if self.thread_type == ChatThreadType.QUOTE and self.quote_id is None:
            raise ValueError("quote_id is required when thread_type='quote'.")
        if self.thread_type == ChatThreadType.ORDER and self.order_id is None:
            raise ValueError("order_id is required when thread_type='order'.")
        return self


class ChatThreadListResponse(PGIBase):
    """Cursor-paginated thread list (GET /api/v1/chat/threads)."""

    threads: list[ChatThreadSchema]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Thread_Participant (contract §2.60)
# ──────────────────────────────────────────────────────────────────────────

class ThreadParticipantSchema(PGIBase):
    """A participant in a chat thread.

    Composite PK: (thread_id, user_id).
    participant_type: 'buyer' | 'vendor' (CN-7 / §3.90).
    """

    thread_id: UUID
    user_id: UUID
    participant_type: ThreadParticipantType
    joined_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Chat_Message (contract §2.61)
# ──────────────────────────────────────────────────────────────────────────

class ChatMessageSchema(PGIBase):
    """A single message within a chat thread.

    sender_type: 'buyer' | 'vendor' | 'system' (CN-8: system messages added).
    message_type: text | file | offer | status_update | system.
    offer_payload_json: present only when message_type = 'offer'.
    attachment_url: presigned S3 URL; null unless message_type = 'file'.
    """

    message_id: UUID
    thread_id: UUID
    sender_user_id: UUID
    sender_type: ChatMessageSenderType
    message_text: Optional[str] = None
    attachment_url: Optional[str] = Field(default=None, max_length=1024)
    message_type: ChatMessageType
    offer_payload_json: Optional[dict[str, Any]] = None
    sent_at: datetime
    read_at: Optional[datetime] = None
    delivery_status: ChatMessageDeliveryStatus

    # Expanded sub-resource
    offer_event: Optional["OfferEventSchema"] = None


class ChatMessageListResponse(PGIBase):
    """Cursor-paginated message list (GET /api/v1/chat/threads/{id}/messages)."""

    messages: list[ChatMessageSchema]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/chat/threads/{id}/messages
# ──────────────────────────────────────────────────────────────────────────

class OfferPayloadRequest(PGIBase):
    """Structured offer payload nested inside a chat message creation request."""

    offer_type: OfferEventType
    proposed_value_json: dict[str, Any] = Field(
        description="Proposed new value (e.g. {'unit_price': '4.50', 'currency': 'USD'})."
    )
    original_value_json: dict[str, Any] = Field(
        description="Current value being negotiated (for context and diff display)."
    )


class SendMessageRequest(PGIBase):
    """POST /api/v1/chat/threads/{id}/messages.

    Idempotency-Key header required.
    Errors: 403 acl_violation (not a thread participant), 422 file_virus_detected.
    """

    message_type: ChatMessageType
    message_text: Optional[str] = Field(default=None, max_length=10000)
    attachment_url: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Presigned S3 URL from Document vault upload.",
    )
    offer_payload_json: Optional[OfferPayloadRequest] = None

    @model_validator(mode="after")
    def validate_message_content(self) -> "SendMessageRequest":
        if self.message_type == ChatMessageType.TEXT and not self.message_text:
            raise ValueError("message_text required when message_type='text'.")
        if self.message_type == ChatMessageType.FILE and not self.attachment_url:
            raise ValueError("attachment_url required when message_type='file'.")
        if self.message_type == ChatMessageType.OFFER and not self.offer_payload_json:
            raise ValueError("offer_payload_json required when message_type='offer'.")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Offer_Event (contract §2.62)
# ──────────────────────────────────────────────────────────────────────────

class OfferEventSchema(PGIBase):
    """A structured negotiation offer attached to a chat message.

    accepted: null (pending), true (accepted), false (rejected).
    resulting_quote_revision_id: set when accepted = True (mirrors to Quote_Revision).
    """

    offer_id: UUID
    message_id: UUID
    offer_type: OfferEventType
    proposed_value_json: dict[str, Any]
    original_value_json: dict[str, Any]
    accepted: Optional[bool] = None
    responded_at: Optional[datetime] = None
    resulting_quote_revision_id: Optional[UUID] = None


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/chat/threads/{id}/offers/{msg_id}/accept
# ──────────────────────────────────────────────────────────────────────────

class AcceptOfferResponse(PGIBase):
    """Response after accepting a structured offer."""

    offer_id: UUID
    accepted: bool = True
    resulting_quote_revision_id: Optional[UUID] = None


class RejectOfferRequest(PGIBase):
    """Optional rejection reason for a structured offer."""

    reason: Optional[str] = Field(default=None, max_length=1000)


class RejectOfferResponse(PGIBase):
    """Response after rejecting a structured offer."""

    offer_id: UUID
    accepted: bool = False


# ──────────────────────────────────────────────────────────────────────────
# WebSocket frame schemas (WS /ws/chat — contract §4.8)
# ──────────────────────────────────────────────────────────────────────────

class WSInboundSendMessage(PGIBase):
    """Client → server: send a chat message via WebSocket."""

    op: str = Field(default="send_message")
    thread_id: UUID
    payload: dict[str, Any]


class WSInboundTyping(PGIBase):
    """Client → server: user is typing indicator."""

    op: str = Field(default="typing")
    thread_id: UUID


class WSOutboundMessage(PGIBase):
    """Server → client: new chat message broadcast."""

    op: str = Field(default="message")
    thread_id: UUID
    message: ChatMessageSchema


class WSOutboundStatus(PGIBase):
    """Server → client: thread status change (e.g. RESOLVED)."""

    op: str = Field(default="status")
    thread_id: UUID
    status: ChatThreadStatus


class WSOutboundNotification(PGIBase):
    """Server → client: real-time notification push."""

    op: str = Field(default="notification")
    notification_id: UUID
    type: str
    title: str
    body: str
    action_url: Optional[str] = None


class WSOutboundOrderStatus(PGIBase):
    """Server → client: PO status change push."""

    op: str = Field(default="order_status_changed")
    po_id: UUID
    new_status: PurchaseOrderStatus
    occurred_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Document (contract §2.63)
# ──────────────────────────────────────────────────────────────────────────

class DocumentSchema(PGIBase):
    """Document vault entry — files attached to any entity.

    Repo C scans every upload for viruses before marking status CLEAN.
    Files shared in chat hold a reference to a Document row (never duplicated).
    s3_url: S3 presigned URL base; expiry managed server-side.
    """

    document_id: UUID
    entity_type: DocumentEntityType
    entity_id: UUID
    s3_url: str = Field(max_length=1024)
    mime_type: str = Field(max_length=128)
    size_bytes: int = Field(ge=0)
    virus_scan_status: DocumentVirusScanStatus
    uploaded_by: UUID
    uploaded_at: datetime


class DocumentUploadResponse(PGIBase):
    """Response after a document is uploaded and scanned."""

    document_id: UUID
    s3_url: str
    virus_scan_status: DocumentVirusScanStatus
    size_bytes: int


# Forward reference resolution
ChatThreadSchema.model_rebuild()
ChatMessageSchema.model_rebuild()
