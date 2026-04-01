"""Chat routes — project threads, messages, attachments, read receipts."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.collaboration import (
    ChatThreadCreate,
    ChatThreadSchema,
    ChatThreadListResponse,
    ChatMessageSchema,
    ChatMessageCreate,
    ChatThreadMessagesResponse,
)
from app.services import collaboration_service
from app.utils.dependencies import require_user, require_roles

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/threads", response_model=ChatThreadListResponse)
def list_threads(
    project_id: str = Query(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        return collaboration_service.list_threads(db, project_id, user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/threads", response_model=ChatThreadSchema, status_code=201)
def create_thread(
    body: ChatThreadCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        thread = collaboration_service.create_thread(
            db=db,
            user=user,
            project_id=body.project_id,
            thread_type=body.thread_type,
            title=body.title,
            is_internal_only=body.is_internal_only,
            rfq_batch_id=body.rfq_batch_id,
            vendor_id=body.vendor_id,
            metadata=body.metadata,
        )
        db.commit()
        return collaboration_service.serialize_thread(db, thread, user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/threads/{thread_id}/messages", response_model=ChatThreadMessagesResponse)
def get_thread_messages(
    thread_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        return collaboration_service.get_thread_messages(db, thread_id, user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/messages", response_model=ChatMessageSchema, status_code=201)
def post_message(
    thread_id: str = Form(...),
    body: str = Form(...),
    message_type: str = Form("message"),
    is_internal_only: bool = Form(True),
    reply_to_message_id: Optional[str] = Form(None),
    metadata_json: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        metadata = {}
        if metadata_json:
            import json
            metadata = json.loads(metadata_json)

        message = collaboration_service.post_message(
            db=db,
            user=user,
            thread_id=thread_id,
            body=body,
            message_type=message_type,
            is_internal_only=is_internal_only,
            reply_to_message_id=reply_to_message_id,
            metadata=metadata,
            files=attachments,
        )
        db.commit()
        return collaboration_service.serialize_message(db, message)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from app.models.collaboration import MessageAttachment, ChatMessage, ChatThread
    attachment = db.query(MessageAttachment).filter(MessageAttachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    message = db.query(ChatMessage).filter(ChatMessage.id == attachment.message_id).first()
    thread = db.query(ChatThread).filter(ChatThread.id == message.thread_id).first() if message else None
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    try:
        collaboration_service._require_project_access(
            db.query(__import__("app.models.project", fromlist=["Project"]).Project).filter(
                __import__("app.models.project", fromlist=["Project"]).Project.id == thread.project_id
            ).first(),
            user,
            thread,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # File serving is intentionally lightweight here. Replace with object storage in production.
    from fastapi.responses import FileResponse
    return FileResponse(
        attachment.file_path,
        media_type=attachment.mime_type or "application/octet-stream",
        filename=attachment.file_name,
    )