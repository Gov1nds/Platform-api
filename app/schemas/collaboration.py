"""Collaboration schemas — threads, messages, attachments, approvals."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MessageAttachmentCreate(BaseModel):
    file_name: str
    file_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatThreadCreate(BaseModel):
    project_id: str
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    thread_type: str = "project"  # project | rfq | vendor | internal | approval
    title: str = "Conversation"
    is_internal_only: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatThreadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    created_by_user_id: Optional[str] = None
    thread_type: str = "project"
    title: str
    is_internal_only: bool = True
    status: str = "active"
    last_message_at: Optional[datetime] = None
    last_message_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    unread_count: int = 0
    last_message: Optional[Dict[str, Any]] = None
    access: Optional[Dict[str, Any]] = None


class ChatThreadListResponse(BaseModel):
    project_id: str
    threads: List[ChatThreadSchema] = Field(default_factory=list)
    pending_approvals: List[Dict[str, Any]] = Field(default_factory=list)
    notification_counts: Dict[str, int] = Field(default_factory=dict)
    access: Optional[Dict[str, Any]] = None


class ChatMessageCreate(BaseModel):
    thread_id: str
    body: str
    message_type: str = "message"  # message | note | approval | system
    is_internal_only: bool = True
    reply_to_message_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    thread_id: str
    sender_user_id: Optional[str] = None
    sender_name: Optional[str] = None
    sender_role: Optional[str] = None
    body: str
    message_type: str = "message"
    is_internal_only: bool = True
    reply_to_message_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    read_by: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    access: Optional[Dict[str, Any]] = None


class ChatThreadMessagesResponse(BaseModel):
    thread: ChatThreadSchema
    messages: List[ChatMessageSchema] = Field(default_factory=list)
    access: Optional[Dict[str, Any]] = None


class ApprovalCreateRequest(BaseModel):
    project_id: str
    thread_id: Optional[str] = None
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    required_role: str = "manager"
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    assigned_to_user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApprovalRequestSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    thread_id: Optional[str] = None
    rfq_batch_id: Optional[str] = None
    vendor_id: Optional[str] = None
    requested_by_user_id: Optional[str] = None
    assigned_to_user_id: Optional[str] = None
    required_role: str = "manager"
    title: str
    description: Optional[str] = None
    status: str = "pending"
    due_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ApprovalDecisionRequest(BaseModel):
    note: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)