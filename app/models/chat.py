"""
Chat, message, offer-event, and document entities.

Contract anchors
----------------
§2.59 Chat_Thread          §2.60 Thread_Participant
§2.61 Chat_Message         §2.62 Offer_Event
§2.63 Document

State vocabularies
------------------
§3.15 SM-012 Chat_Thread.status    §3.49 Chat_Thread.thread_type
§3.50 Chat_Message.message_type    §3.51 Chat_Message.sender_type (+ ``system`` per CN-8)
§3.52 Chat_Message.delivery_status §3.53 Offer_Event.offer_type
§3.54 Document.entity_type         §3.55 Document.virus_scan_status
§3.90 Thread_Participant.participant_type

Notes
-----
* ``thread_type='quote'`` requires non-null ``quote_id``; ``thread_type='order'``
  requires non-null ``order_id``. Enforced at the DB layer.
* Only Repo C writes ``sender_type = 'system'`` (CN-8).
* ``document`` is the authoritative store for all attachments referenced
  elsewhere (e.g. ``goods_receipt.attachments_json`` cache).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_object,
    jsonb_object_nullable,
    tstz,
    uuid_fk,
    uuid_pk,
    uuid_polymorphic,
)
from app.models.enums import (
    ChatMessageDeliveryStatus,
    ChatMessageSenderType,
    ChatMessageType,
    ChatThreadStatus,
    ChatThreadType,
    DocumentEntityType,
    DocumentVirusScanStatus,
    OfferEventType,
    ThreadParticipantType,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# ChatThread (§2.59)
# ─────────────────────────────────────────────────────────────────────────────


class ChatThread(Base):
    """Buyer ↔ vendor chat thread, optionally bound to a quote / RFQ / order."""

    __tablename__ = "chat_thread"

    thread_id: Mapped[uuid.UUID] = uuid_pk()
    thread_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_id: Mapped[uuid.UUID | None] = uuid_fk(
        "quote.quote_id", ondelete="SET NULL", nullable=True
    )
    rfq_id: Mapped[uuid.UUID | None] = uuid_fk(
        "rfq.rfq_id", ondelete="SET NULL", nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = uuid_fk(
        "purchase_order.po_id", ondelete="SET NULL", nullable=True
    )
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="RESTRICT"
    )
    buyer_user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    created_at: Mapped[datetime] = tstz(default_now=True)
    last_message_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("thread_type", values_of(ChatThreadType)),
        enum_check("status", values_of(ChatThreadStatus)),
        CheckConstraint(
            "thread_type <> 'quote' OR quote_id IS NOT NULL",
            name="quote_thread_requires_quote_id",
        ),
        CheckConstraint(
            "thread_type <> 'order' OR order_id IS NOT NULL",
            name="order_thread_requires_order_id",
        ),
        Index(
            "ix_chat_thread_vendor_id_buyer_user_id_thread_type",
            "vendor_id",
            "buyer_user_id",
            "thread_type",
        ),
        Index("ix_chat_thread_quote_id", "quote_id"),
        Index("ix_chat_thread_order_id", "order_id"),
        Index("ix_chat_thread_last_message_at", "last_message_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ThreadParticipant (§2.60)
# ─────────────────────────────────────────────────────────────────────────────


class ThreadParticipant(Base, CreatedAtMixin):
    """Participant in a chat thread (composite PK)."""

    __tablename__ = "thread_participant"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_thread.thread_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    participant_type: Mapped[str] = mapped_column(String(8), nullable=False)
    joined_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("participant_type", values_of(ThreadParticipantType)),
        Index("ix_thread_participant_user_id", "user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ChatMessage (§2.61)
# ─────────────────────────────────────────────────────────────────────────────


class ChatMessage(Base, CreatedAtMixin):
    """Message within a chat thread. May carry a structured offer payload."""

    __tablename__ = "chat_message"

    message_id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = uuid_fk(
        "chat_thread.thread_id", ondelete="CASCADE"
    )
    sender_user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    sender_type: Mapped[str] = mapped_column(String(8), nullable=False)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    message_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'text'")
    )
    offer_payload_json: Mapped[dict | None] = jsonb_object_nullable()
    sent_at: Mapped[datetime] = tstz(default_now=True)
    read_at: Mapped[datetime | None] = tstz(nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )

    __table_args__ = (
        enum_check("sender_type", values_of(ChatMessageSenderType)),
        enum_check("message_type", values_of(ChatMessageType)),
        enum_check("delivery_status", values_of(ChatMessageDeliveryStatus)),
        Index("ix_chat_message_thread_id_sent_at", "thread_id", "sent_at"),
        Index("ix_chat_message_sender_user_id_sent_at", "sender_user_id", "sent_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OfferEvent (§2.62)
# ─────────────────────────────────────────────────────────────────────────────


class OfferEvent(Base, CreatedAtMixin):
    """Structured offer embedded in a chat message (price / lead-time /
    quantity / combined). On acceptance, a Quote_Revision is generated."""

    __tablename__ = "offer_event"

    offer_id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = uuid_fk(
        "chat_message.message_id", ondelete="CASCADE"
    )
    offer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_value_json: Mapped[dict] = jsonb_object()
    original_value_json: Mapped[dict] = jsonb_object()
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    responded_at: Mapped[datetime | None] = tstz(nullable=True)
    resulting_quote_revision_id: Mapped[uuid.UUID | None] = uuid_fk(
        "quote_revision.revision_id", ondelete="SET NULL", nullable=True
    )

    __table_args__ = (
        enum_check("offer_type", values_of(OfferEventType)),
        Index("ix_offer_event_message_id", "message_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Document (§2.63)
# ─────────────────────────────────────────────────────────────────────────────


class Document(Base, CreatedAtMixin):
    """Authoritative document store for all attachments.

    ``entity_id`` is polymorphic — integrity of the pointer is enforced in
    the service layer (application + audit).
    """

    __tablename__ = "document"

    document_id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    s3_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    virus_scan_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'PENDING'")
    )
    uploaded_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    uploaded_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("entity_type", values_of(DocumentEntityType)),
        enum_check("virus_scan_status", values_of(DocumentVirusScanStatus)),
        CheckConstraint("size_bytes >= 0", name="size_bytes_nonneg"),
        Index("ix_document_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_document_virus_scan_status", "virus_scan_status"),
    )


__all__ = [
    "ChatThread",
    "ThreadParticipant",
    "ChatMessage",
    "OfferEvent",
    "Document",
]
