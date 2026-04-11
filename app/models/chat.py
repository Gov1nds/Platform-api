"""
Chat thread and message models with structured offer support.

References: GAP-028, GAP-018
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        Index("ix_ct_context", "context_type", "context_id"),
        Index("ix_ct_org", "organization_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    context_type = Column(Text, nullable=False)  # project, rfq, line_item
    context_id = Column(UUID(as_uuid=False), nullable=False)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    title = Column(Text, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship("ChatMessage", back_populates="thread", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_cm_thread", "thread_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    thread_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ops.chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_user_id = Column(UUID(as_uuid=False), nullable=True)
    sender_vendor_user_id = Column(UUID(as_uuid=False), nullable=True)
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    visibility = Column(Text, nullable=False, default="internal")  # internal, vendor_visible
    message_type = Column(String(40), nullable=False, default="text")  # text, offer, system, attachment
    content = Column(Text, nullable=False)
    attachment_url = Column(Text, nullable=True)
    offer_event_json = Column(JSONB, nullable=True)  # structured offer data
    message_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)

    thread = relationship("ChatThread", back_populates="messages")