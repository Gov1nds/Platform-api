"""Chat routes — project threads, messages, attachments, read receipts."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.project import Project
from app.schemas.collaboration import (
    ChatThreadCreate,
    ChatThreadSchema,
    ChatThreadListResponse,
    ChatMessageSchema,
    ChatMessageCreate,
    ChatThreadMessagesResponse,
)
from app.services import collaboration_service
from app.services.workflow_service import begin_command, complete_command, fail_command
from app.utils.dependencies import require_user, build_project_access_context, can_access_project

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/threads", response_model=ChatThreadListResponse)
def list_threads(
    project_id: str = Query(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        response = collaboration_service.list_threads(db, project_id, user)
        project = db.query(Project).filter(Project.id == project_id).first()
        response["access"] = build_project_access_context(user, project, db)
        return response
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/threads", response_model=ChatThreadSchema, status_code=201)
def create_thread(
    body: ChatThreadCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    command, cached = begin_command(
        db,
        namespace="chat.thread.create",
        idempotency_key=idempotency_key,
        payload=body.model_dump(mode="json"),
        request_method="POST",
        request_path="/api/v1/chat/threads",
        user_id=user.id,
        project_id=body.project_id,
        related_id=body.rfq_batch_id or body.vendor_id or body.project_id,
    )
    if cached:
        return ChatThreadSchema.model_validate(cached)

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
        response = collaboration_service.serialize_thread(db, thread, user)
        project = db.query(Project).filter(Project.id == thread.project_id).first()
        response.access = build_project_access_context(user, project, db)
        complete_command(db, command, response.model_dump(mode="json"))
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


@router.get("/threads/{thread_id}/messages", response_model=ChatThreadMessagesResponse)
def get_thread_messages(
    thread_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        response = collaboration_service.get_thread_messages(db, thread_id, user)
        thread = collaboration_service.get_thread(db, thread_id, user)
        project = db.query(Project).filter(Project.id == thread.project_id).first()
        response.access = build_project_access_context(user, project, db)
        return response
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
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    try:
        metadata = {}
        if metadata_json:
            import json
            metadata = json.loads(metadata_json)

        command, cached = begin_command(
            db,
            namespace="chat.message.post",
            idempotency_key=idempotency_key,
            payload={
                "thread_id": thread_id,
                "body": body,
                "message_type": message_type,
                "is_internal_only": is_internal_only,
                "reply_to_message_id": reply_to_message_id,
                "metadata": metadata,
                "attachments": [a.filename for a in attachments],
                "user_id": user.id,
            },
            request_method="POST",
            request_path="/api/v1/chat/messages",
            user_id=user.id,
            related_id=thread_id,
        )
        if cached:
            return ChatMessageSchema.model_validate(cached)

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
        response = collaboration_service.serialize_message(db, message)
        complete_command(db, command, response.model_dump(mode="json"))
        db.commit()
        return response
    except ValueError as e:
        try:
            fail_command(db, command, str(e))  # type: ignore[name-defined]
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        try:
            fail_command(db, command, str(e))  # type: ignore[name-defined]
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        try:
            fail_command(db, command, str(e))  # type: ignore[name-defined]
        except Exception:
            pass
        db.rollback()
        raise


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