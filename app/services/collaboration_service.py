"""Collaboration service — chat threads, messages, receipts, approvals, audit trail."""
from __future__ import annotations

import os
import shutil
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models.collaboration import (
    ChatThread,
    ChatMessage,
    MessageAttachment,
    ChatReadReceipt,
    ApprovalRequest,
    ApprovalAction,
)
from app.models.project import Project
from app.models.project_access import ProjectParticipant
from app.models.rfq import RFQBatch
from app.models.vendor import Vendor
from app.models.user import User
from app.services import project_service
from app.services.storage_service import save_bytes, load_bytes
from app.services.integration_service import register_document_asset
from app.utils.dependencies import is_collaboration_role

logger = logging.getLogger("collaboration_service")

ATTACHMENT_DIR = Path(os.getenv("COLLAB_ATTACHMENT_DIR", "uploads/chat"))


def _safe_role(value: Optional[str]) -> str:
    return (value or "user").lower().strip()


def _vendor_identity(user: Optional[User]) -> Optional[str]:
    if not user:
        return None
    metadata = getattr(user, "metadata_", None) or {}
    if isinstance(metadata, dict):
        vendor_id = metadata.get("vendor_id") or metadata.get("supplier_id") or metadata.get("organization_id")
        if vendor_id:
            return str(vendor_id)
        vendor_email = metadata.get("vendor_email")
        if vendor_email:
            return str(vendor_email).lower()
    return None


def _project_owner_or_collaborator(project: Project, user: User) -> bool:
    if not project or not user:
        return False
    if getattr(project, "user_id", None) and str(project.user_id) == str(user.id):
        return True
    try:
        from app.utils.dependencies import can_access_project
        return can_access_project(user, project)
    except Exception:
        return False


def _require_project_access(project: Project, user: User, thread: Optional[ChatThread] = None):
    if not project:
        raise ValueError("Project not found")

    role = _safe_role(getattr(user, "role", None))
    if role == "admin":
        return True

    if _project_owner_or_collaborator(project, user):
        return True

    if thread and not thread.is_internal_only:
        vendor_identity = _vendor_identity(user)
        if vendor_identity and str(thread.vendor_id or "") in {vendor_identity, str(thread.vendor_id)}:
            return True
        if role in {"vendor", "manager", "sourcing", "buyer"}:
            return True

    raise PermissionError("Not authorized")


def _require_approval_access(db: Session, approval: ApprovalRequest, user: User):
    if not approval:
        raise ValueError("Approval request not found")

    role = _safe_role(getattr(user, "role", None))
    if role == "admin":
        return True

    project = db.query(Project).filter(Project.id == approval.project_id).first()
    if project and _project_owner_or_collaborator(project, user):
        return True

    if user and approval.requested_by_user_id and str(approval.requested_by_user_id) == str(user.id):
        return True
    if user and approval.assigned_to_user_id and str(approval.assigned_to_user_id) == str(user.id):
        return True
    if user and role == _safe_role(approval.required_role):
        return True
    if user and role in {"manager", "sourcing", "buyer"} and _safe_role(approval.required_role) in {"manager", "buyer", "sourcing"}:
        return True
    raise PermissionError("Not authorized for this approval")


def _thread_last_message(db: Session, thread_id: str) -> Optional[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc())
        .first()
    )


def _serialize_user(user: Optional[User]) -> Optional[Dict[str, Any]]:
    if not user:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
    }


def _serialize_attachments(db: Session, message_id: str) -> List[Dict[str, Any]]:
    attachments = (
        db.query(MessageAttachment)
        .filter(MessageAttachment.message_id == message_id)
        .order_by(MessageAttachment.created_at.asc())
        .all()
    )
    return [
        {
            "id": a.id,
            "message_id": a.message_id,
            "uploaded_by_user_id": a.uploaded_by_user_id,
            "file_name": a.file_name,
            "file_path": a.file_path,
            "mime_type": a.mime_type,
            "file_size": a.file_size,
            "file_url": a.file_url,
            "metadata": a.metadata_ or {},
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in attachments
    ]


def _serialize_receipts(db: Session, message_id: str) -> List[Dict[str, Any]]:
    receipts = (
        db.query(ChatReadReceipt)
        .filter(ChatReadReceipt.message_id == message_id)
        .order_by(ChatReadReceipt.read_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "thread_id": r.thread_id,
            "message_id": r.message_id,
            "user_id": r.user_id,
            "read_at": r.read_at.isoformat() if r.read_at else None,
        }
        for r in receipts
    ]


def serialize_message(db: Session, message: ChatMessage) -> Dict[str, Any]:
    sender = db.query(User).filter(User.id == message.sender_user_id).first() if message.sender_user_id else None
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "sender_user_id": message.sender_user_id,
        "sender_name": sender.full_name if sender else None,
        "sender_role": sender.role if sender else None,
        "body": message.body,
        "message_type": message.message_type,
        "is_internal_only": message.is_internal_only,
        "reply_to_message_id": message.reply_to_message_id,
        "metadata": message.metadata_ or {},
        "attachments": _serialize_attachments(db, message.id),
        "read_by": _serialize_receipts(db, message.id),
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _thread_unread_count(db: Session, thread_id: str, user_id: str) -> int:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    if not messages:
        return 0

    read_message_ids = {
        r.message_id
        for r in db.query(ChatReadReceipt.message_id)
        .filter(ChatReadReceipt.thread_id == thread_id, ChatReadReceipt.user_id == user_id)
        .all()
    }
    return sum(1 for m in messages if m.id not in read_message_ids and str(m.sender_user_id) != str(user_id))


def serialize_thread(db: Session, thread: ChatThread, user: Optional[User] = None) -> Dict[str, Any]:
    last_msg = _thread_last_message(db, thread.id)
    unread_count = _thread_unread_count(db, thread.id, user.id) if user else 0
    return {
        "id": thread.id,
        "project_id": thread.project_id,
        "rfq_batch_id": thread.rfq_batch_id,
        "vendor_id": thread.vendor_id,
        "created_by_user_id": thread.created_by_user_id,
        "thread_type": thread.thread_type,
        "title": thread.title,
        "is_internal_only": thread.is_internal_only,
        "status": thread.status,
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "last_message_id": thread.last_message_id,
        "metadata": thread.metadata_ or {},
        "unread_count": unread_count,
        "last_message": serialize_message(db, last_msg) if last_msg else None,
    }


def _touch_thread(db: Session, thread: ChatThread, message: ChatMessage):
    thread.last_message_at = message.created_at or datetime.utcnow()
    thread.last_message_id = message.id
    db.flush()


def create_thread(
    db: Session,
    user: User,
    project_id: str,
    thread_type: str = "project",
    title: str = "Conversation",
    is_internal_only: bool = True,
    rfq_batch_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ChatThread:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Project not found")
    _require_project_access(project, user)

    rfq = None
    if rfq_batch_id:
        rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_batch_id).first()
        if not rfq:
            raise ValueError("RFQ not found")
        if str(rfq.project_id) != str(project.id):
            raise ValueError("RFQ does not belong to project")

    vendor = None
    if vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise ValueError("Vendor not found")

    thread = ChatThread(
        project_id=project.id,
        rfq_batch_id=rfq.id if rfq else None,
        vendor_id=vendor.id if vendor else None,
        created_by_user_id=user.id,
        thread_type=thread_type,
        title=title or "Conversation",
        is_internal_only=bool(is_internal_only),
        metadata_=metadata or {},
    )
    db.add(thread)
    db.flush()

    project_service._emit_event(
        db,
        project,
        "chat_thread_created",
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        {
            "thread_id": thread.id,
            "thread_type": thread.thread_type,
            "title": thread.title,
            "rfq_batch_id": thread.rfq_batch_id,
            "vendor_id": thread.vendor_id,
            "is_internal_only": thread.is_internal_only,
        },
        actor_user_id=user.id,
    )
    return thread


def list_threads(db: Session, project_id: str, user: User) -> Dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Project not found")
    _require_project_access(project, user)

    threads = (
        db.query(ChatThread)
        .filter(ChatThread.project_id == project.id)
        .order_by(ChatThread.last_message_at.desc().nullslast(), ChatThread.created_at.desc())
        .all()
    )

    approvals = list_approvals(db, project.id, user)

    serialized = [serialize_thread(db, t, user) for t in threads]
    counts = {
        "threads": len(serialized),
        "unread_messages": sum(t["unread_count"] for t in serialized),
        "pending_approvals": len([a for a in approvals if a["status"] == "pending"]),
        "internal_threads": len([t for t in serialized if t["is_internal_only"]]),
        "vendor_threads": len([t for t in serialized if t["thread_type"] == "vendor"]),
    }

    return {
        "project_id": project.id,
        "threads": serialized,
        "pending_approvals": approvals,
        "notification_counts": counts,
    }


def get_thread(db: Session, thread_id: str, user: User) -> ChatThread:
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        raise ValueError("Thread not found")
    project = db.query(Project).filter(Project.id == thread.project_id).first()
    _require_project_access(project, user, thread)
    return thread


def mark_thread_read(db: Session, thread: ChatThread, user: User):
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    if not messages:
        return

    existing = {
        r.message_id
        for r in db.query(ChatReadReceipt.message_id)
        .filter(ChatReadReceipt.thread_id == thread.id, ChatReadReceipt.user_id == user.id)
        .all()
    }
    new_receipts = []
    for msg in messages:
        if str(msg.sender_user_id) == str(user.id):
            continue
        if msg.id in existing:
            continue
        new_receipts.append(ChatReadReceipt(
            thread_id=thread.id,
            message_id=msg.id,
            user_id=user.id,
        ))
    if new_receipts:
        db.add_all(new_receipts)
        db.flush()


def _store_attachments(
    db: Session,
    message: ChatMessage,
    user: User,
    files,
) -> List[MessageAttachment]:
    created = []
    if not files:
        return created

    for file in files:
        if not file:
            continue
        safe_name = file.filename or f"{uuid.uuid4().hex}.bin"
        contents = file.file.read()
        stored = save_bytes(
            contents or b"",
            safe_name,
            scope=f"chat/{message.thread_id}/{message.id}",
            content_type=file.content_type,
            prefix="chat/",
        )

        attachment = MessageAttachment(
            message_id=message.id,
            uploaded_by_user_id=user.id,
            file_name=file.filename or safe_name,
            file_path=stored.storage_key,
            mime_type=file.content_type,
            file_size=stored.file_size_bytes,
            file_url=f"/api/v1/chat/attachments/{uuid.uuid4().hex}",
            metadata_={
                "storage_provider": stored.provider,
                "storage_key": stored.storage_key,
                "sha256": stored.sha256,
                "saved_locally": stored.provider == "local",
            },
        )
        db.add(attachment)
        db.flush()
        register_document_asset(
            db,
            source_type="attachment",
            source_id=str(attachment.id),
            storage_provider=stored.provider,
            storage_key=stored.storage_key,
            file_name=attachment.file_name,
            mime_type=file.content_type,
            file_size_bytes=stored.file_size_bytes,
            sha256=stored.sha256,
            project_id=str(message.thread.project_id),
            rfq_batch_id=str(message.thread.rfq_batch_id) if message.thread.rfq_batch_id else None,
            vendor_id=str(message.thread.vendor_id) if message.thread.vendor_id else None,
            uploaded_by_user_id=str(user.id),
            asset_kind="attachment",
            public_url=stored.public_url,
            metadata={"thread_id": str(message.thread_id), "message_id": str(message.id)},
        )
        attachment.file_url = f"/api/v1/chat/attachments/{attachment.id}"
        created.append(attachment)
    return created


def post_message(
    db: Session,
    user: User,
    thread_id: str,
    body: str,
    message_type: str = "message",
    is_internal_only: bool = True,
    reply_to_message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    files=None,
) -> ChatMessage:
    thread = get_thread(db, thread_id, user)
    project = db.query(Project).filter(Project.id == thread.project_id).first()
    if not project:
        raise ValueError("Project not found")

    if thread.is_internal_only and _safe_role(user.role) not in {"admin", "manager", "sourcing", "buyer"} and str(project.user_id) != str(user.id):
        raise PermissionError("Not authorized to post in this thread")

    msg = ChatMessage(
        thread_id=thread.id,
        sender_user_id=user.id,
        body=body,
        message_type=message_type or "message",
        is_internal_only=bool(is_internal_only),
        reply_to_message_id=reply_to_message_id,
        metadata_=metadata or {},
    )
    db.add(msg)
    db.flush()

    if files:
        _store_attachments(db, msg, user, files)

    _touch_thread(db, thread, msg)
    mark_thread_read(db, thread, user)

    project_service._emit_event(
        db,
        project,
        "chat_message_posted",
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        {
            "thread_id": thread.id,
            "message_id": msg.id,
            "message_type": msg.message_type,
            "is_internal_only": msg.is_internal_only,
        },
        actor_user_id=user.id,
    )

    db.flush()
    return msg


def get_thread_messages(db: Session, thread_id: str, user: User) -> Dict[str, Any]:
    thread = get_thread(db, thread_id, user)
    mark_thread_read(db, thread, user)

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    db.flush()

    return {
        "thread": serialize_thread(db, thread, user),
        "messages": [serialize_message(db, m) for m in messages],
    }


def list_approvals(db: Session, project_id: str, user: User) -> List[Dict[str, Any]]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Project not found")
    _require_project_access(project, user)

    approvals = (
        db.query(ApprovalRequest)
        .options(joinedload(ApprovalRequest.actions))
        .filter(ApprovalRequest.project_id == project.id)
        .order_by(ApprovalRequest.created_at.desc())
        .all()
    )
    payload = []
    for a in approvals:
        payload.append({
            "id": a.id,
            "project_id": a.project_id,
            "thread_id": a.thread_id,
            "rfq_batch_id": a.rfq_batch_id,
            "vendor_id": a.vendor_id,
            "requested_by_user_id": a.requested_by_user_id,
            "assigned_to_user_id": a.assigned_to_user_id,
            "required_role": a.required_role,
            "title": a.title,
            "description": a.description,
            "status": a.status,
            "due_at": a.due_at.isoformat() if a.due_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
            "resolution_note": a.resolution_note,
            "metadata": a.metadata_ or {},
            "actions": [
                {
                    "id": ac.id,
                    "approval_request_id": ac.approval_request_id,
                    "acted_by_user_id": ac.acted_by_user_id,
                    "action": ac.action,
                    "note": ac.note,
                    "metadata": ac.metadata_ or {},
                    "created_at": ac.created_at.isoformat() if ac.created_at else None,
                }
                for ac in a.actions
            ],
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        })
    return payload


def create_approval_request(
    db: Session,
    user: User,
    project_id: str,
    title: str,
    description: Optional[str] = None,
    required_role: str = "manager",
    thread_id: Optional[str] = None,
    rfq_batch_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    assigned_to_user_id: Optional[str] = None,
    due_at=None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ApprovalRequest:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Project not found")
    _require_project_access(project, user)

    req = ApprovalRequest(
        project_id=project.id,
        thread_id=thread_id,
        rfq_batch_id=rfq_batch_id,
        vendor_id=vendor_id,
        requested_by_user_id=user.id,
        assigned_to_user_id=assigned_to_user_id,
        required_role=required_role or "manager",
        title=title,
        description=description,
        due_at=due_at,
        metadata_=metadata or {},
        status="pending",
    )
    db.add(req)
    db.flush()

    if thread_id:
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if thread:
            note = ChatMessage(
                thread_id=thread.id,
                sender_user_id=user.id,
                body=f"Approval requested: {title}",
                message_type="approval",
                is_internal_only=True,
                metadata_={"approval_request_id": req.id, "action": "requested"},
            )
            db.add(note)
            db.flush()
            _touch_thread(db, thread, note)

    project_service._emit_event(
        db,
        project,
        "approval_requested",
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
        {"approval_request_id": req.id, "title": title, "required_role": required_role},
        actor_user_id=user.id,
    )
    return req


def _record_approval_action(
    db: Session,
    approval: ApprovalRequest,
    user: User,
    action: str,
    note: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    approval_action = ApprovalAction(
        approval_request_id=approval.id,
        acted_by_user_id=user.id,
        action=action,
        note=note,
        metadata_=metadata or {},
    )
    db.add(approval_action)
    db.flush()
    return approval_action


def approve_request(db: Session, approval_id: str, user: User, note: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> ApprovalRequest:
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        raise ValueError("Approval request not found")
    _require_approval_access(db, approval, user)

    approval.status = "approved"
    approval.resolved_at = datetime.utcnow()
    approval.resolution_note = note
    approval.updated_at = datetime.utcnow()
    _record_approval_action(db, approval, user, "approve", note=note, metadata=metadata)

    project = db.query(Project).filter(Project.id == approval.project_id).first()
    if project:
        project_service._emit_event(
            db,
            project,
            "approval_approved",
            project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
            project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
            {"approval_request_id": approval.id, "note": note},
            actor_user_id=user.id,
        )
    return approval


def reject_request(db: Session, approval_id: str, user: User, note: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> ApprovalRequest:
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        raise ValueError("Approval request not found")
    _require_approval_access(db, approval, user)

    approval.status = "rejected"
    approval.resolved_at = datetime.utcnow()
    approval.resolution_note = note
    approval.updated_at = datetime.utcnow()
    _record_approval_action(db, approval, user, "reject", note=note, metadata=metadata)

    project = db.query(Project).filter(Project.id == approval.project_id).first()
    if project:
        project_service._emit_event(
            db,
            project,
            "approval_rejected",
            project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
            project.workflow_stage if hasattr(project, "workflow_stage") else project.status,
            {"approval_request_id": approval.id, "note": note},
            actor_user_id=user.id,
        )
    return approval


def get_approval(db: Session, approval_id: str, user: User) -> ApprovalRequest:
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        raise ValueError("Approval request not found")
    _require_approval_access(db, approval, user)
    return approval


def serialize_approval(db: Session, approval: ApprovalRequest) -> Dict[str, Any]:
    return {
        "id": approval.id,
        "project_id": approval.project_id,
        "thread_id": approval.thread_id,
        "rfq_batch_id": approval.rfq_batch_id,
        "vendor_id": approval.vendor_id,
        "requested_by_user_id": approval.requested_by_user_id,
        "assigned_to_user_id": approval.assigned_to_user_id,
        "required_role": approval.required_role,
        "title": approval.title,
        "description": approval.description,
        "status": approval.status,
        "due_at": approval.due_at.isoformat() if approval.due_at else None,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "resolution_note": approval.resolution_note,
        "metadata": approval.metadata_ or {},
        "actions": [
            {
                "id": ac.id,
                "approval_request_id": ac.approval_request_id,
                "acted_by_user_id": ac.acted_by_user_id,
                "action": ac.action,
                "note": ac.note,
                "metadata": ac.metadata_ or {},
                "created_at": ac.created_at.isoformat() if ac.created_at else None,
            }
            for ac in approval.actions
        ],
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "updated_at": approval.updated_at.isoformat() if approval.updated_at else None,
    }