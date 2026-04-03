"""Collaboration and negotiation models — chat, receipts, approvals, audit trail."""
import uuid
from datetime import datetime

from sqlalchemy import Column, Text, DateTime, ForeignKey, Boolean, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        Index("ix_chat_threads_project", "project_id"),
        Index("ix_chat_threads_rfq", "rfq_batch_id"),
        Index("ix_chat_threads_vendor", "vendor_id"),
        Index("ix_chat_threads_type", "thread_type"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    thread_type = Column(Text, nullable=False, default="project")  # project | rfq | vendor | internal | approval
    title = Column(Text, nullable=False, default="Conversation")
    is_internal_only = Column(Boolean, nullable=False, default=True)
    status = Column(Text, nullable=False, default="active")
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    last_message_id = Column(UUID(as_uuid=False), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="chat_threads")
    rfq = relationship("RFQBatch")
    vendor = relationship("Vendor")
    created_by = relationship("User")
    messages = relationship("ChatMessage", back_populates="thread", cascade="all, delete-orphan")
    approvals = relationship("ApprovalRequest", back_populates="thread", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_thread", "thread_id"),
        Index("ix_chat_messages_sender", "sender_user_id"),
        Index("ix_chat_messages_created", "created_at"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.chat_threads.id", ondelete="CASCADE"), nullable=False)
    sender_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    body = Column(Text, nullable=False)
    message_type = Column(Text, nullable=False, default="message")  # message | note | approval | system
    is_internal_only = Column(Boolean, nullable=False, default=True)
    reply_to_message_id = Column(UUID(as_uuid=False), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    thread = relationship("ChatThread", back_populates="messages")
    sender = relationship("User")
    attachments = relationship("MessageAttachment", back_populates="message", cascade="all, delete-orphan")
    receipts = relationship("ChatReadReceipt", back_populates="message", cascade="all, delete-orphan")


class MessageAttachment(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (
        Index("ix_message_attachments_message", "message_id"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.chat_messages.id", ondelete="CASCADE"), nullable=False)
    uploaded_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    file_name = Column(Text, nullable=False)
    file_path = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    file_url = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    message = relationship("ChatMessage", back_populates="attachments")
    uploaded_by = relationship("User")


class ChatReadReceipt(Base):
    __tablename__ = "chat_read_receipts"
    __table_args__ = (
        Index("ix_chat_receipts_thread_user", "thread_id", "user_id"),
        Index("ix_chat_receipts_message", "message_id"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.chat_threads.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.chat_messages.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    thread = relationship("ChatThread")
    message = relationship("ChatMessage", back_populates="receipts")
    user = relationship("User")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_project", "project_id"),
        Index("ix_approval_requests_thread", "thread_id"),
        Index("ix_approval_requests_status", "status"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    thread_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.chat_threads.id", ondelete="SET NULL"), nullable=True)
    rfq_batch_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)

    requested_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    assigned_to_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)

    required_role = Column(Text, nullable=False, default="manager")
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")  # pending | approved | rejected | cancelled
    due_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="approval_requests")
    thread = relationship("ChatThread", back_populates="approvals")
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_user_id])
    participants = relationship("ProjectParticipant", back_populates="approval_request", cascade="all, delete-orphan")
    actions = relationship("ApprovalAction", back_populates="approval_request", cascade="all, delete-orphan")


class ApprovalAction(Base):
    __tablename__ = "approval_actions"
    __table_args__ = (
        Index("ix_approval_actions_request", "approval_request_id"),
        Index("ix_approval_actions_actor", "acted_by_user_id"),
        {"schema": "collaboration"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    approval_request_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.approval_requests.id", ondelete="CASCADE"), nullable=False)
    acted_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    action = Column(Text, nullable=False)  # approve | reject | request | comment | reopen
    note = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    approval_request = relationship("ApprovalRequest", back_populates="actions")
    acted_by = relationship("User")