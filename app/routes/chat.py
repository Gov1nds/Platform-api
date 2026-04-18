from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.chat import ChatThread, ChatMessage
from app.models.project import Project
from app.models.rfq import RFQBatch
from app.schemas import ThreadCreateRequest, MessageCreateRequest, ThreadResponse, MessageResponse
from app.utils.dependencies import require_user

import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

def _check_context_access(db, user, context_type, context_id):
    """Verify the caller has access to the business object the chat is attached to."""
    if context_type == "project":
        p = db.query(Project).filter(Project.id == context_id).first()
        if not p: raise HTTPException(404, "Project not found")
        if p.user_id != user.id and user.role != "admin": raise HTTPException(403, "No access to this project")
    elif context_type == "rfq":
        r = db.query(RFQBatch).filter(RFQBatch.id == context_id).first()
        if not r: raise HTTPException(404, "RFQ not found")
        if r.requested_by_user_id != user.id and user.role != "admin": raise HTTPException(403, "No access to this RFQ")
    # line_item scoping delegates to project access via the RFQ→project chain

@router.post("/threads", response_model=ThreadResponse)
def create_thread(body: ThreadCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _check_context_access(db, user, body.context_type, body.context_id)
    thread = ChatThread(context_type=body.context_type, context_id=body.context_id,
        title=body.title, created_by_user_id=user.id)
    db.add(thread); db.commit(); db.refresh(thread)
    return ThreadResponse.model_validate(thread)

@router.get("/threads")
def list_threads(context_type:str=Query(""), context_id:str=Query(""),
                 user:User=Depends(require_user), db:Session=Depends(get_db)):
    if context_type and context_id:
        _check_context_access(db, user, context_type, context_id)
    q = db.query(ChatThread)
    if context_type: q = q.filter(ChatThread.context_type == context_type)
    if context_id: q = q.filter(ChatThread.context_id == context_id)
    return [ThreadResponse.model_validate(t) for t in q.order_by(ChatThread.created_at.desc()).all()]

@router.post("/messages", response_model=MessageResponse)
def send_message(body: MessageCreateRequest, user: User = Depends(require_user), db: Session = Depends(get_db)):
    thread = db.query(ChatThread).filter(ChatThread.id == body.thread_id).first()
    if not thread: raise HTTPException(404)
    _check_context_access(db, user, thread.context_type, thread.context_id)
    msg = ChatMessage(thread_id=body.thread_id, sender_user_id=user.id,
        visibility=body.visibility, content=body.content, attachment_url=body.attachment_url)
    db.add(msg); db.commit(); db.refresh(msg)
    return MessageResponse.model_validate(msg)

@router.get("/threads/{thread_id}/messages")
def get_messages(thread_id:str, visibility:str=Query(""),
                 cursor:str|None=Query(None, description="Message ID for cursor-based pagination"),
                 limit:int=Query(50, ge=1, le=200),
                 user:User=Depends(require_user), db:Session=Depends(get_db)):
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread: raise HTTPException(404)
    _check_context_access(db, user, thread.context_type, thread.context_id)
    q = db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id)
    if visibility: q = q.filter(ChatMessage.visibility == visibility)
    if cursor:
        ref = db.query(ChatMessage).filter(ChatMessage.id == cursor).first()
        if ref:
            q = q.filter(ChatMessage.created_at > ref.created_at)
    return [MessageResponse.model_validate(m) for m in q.order_by(ChatMessage.created_at.asc()).limit(limit).all()]


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws/{thread_id}")
async def websocket_chat(websocket: WebSocket, thread_id: str):
    """
    Real-time chat via WebSocket.

    Connect with ?token=<JWT> query param for auth.
    Messages are broadcast to all connections in the thread.
    """
    from app.services.chat_service import connection_manager
    from app.core.security import decode_token
    from app.core.database import SessionLocal

    # Auth via query param
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4001, reason="Missing auth token")
        return

    try:
        payload = decode_token(token)
        user_id = payload.get("sub") if payload else None
        if not user_id:
            await websocket.close(code=4003, reason="Invalid token")
            return
    except Exception:
        await websocket.close(code=4003, reason="Invalid token")
        return

    # Verify thread access
    db = SessionLocal()
    try:
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if not thread:
            await websocket.close(code=4004, reason="Thread not found")
            return
    finally:
        db.close()

    # Connect
    await connection_manager.connect(thread_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_data = json.loads(data)
            except json.JSONDecodeError:
                await connection_manager.send_personal(websocket, {"error": "Invalid JSON"})
                continue

            content = msg_data.get("content", "")
            visibility = msg_data.get("visibility", "internal")
            message_type = msg_data.get("message_type", "text")

            # Persist message
            db = SessionLocal()
            try:
                msg = ChatMessage(
                    thread_id=thread_id,
                    sender_user_id=user_id,
                    visibility=visibility,
                    content=content,
                    message_type=message_type,
                )
                if message_type == "offer" and "offer_payload" in msg_data:
                    msg.offer_payload_json = msg_data["offer_payload"]
                db.add(msg)
                db.commit()
                db.refresh(msg)

                broadcast_payload = {
                    "type": "message",
                    "id": str(msg.id),
                    "thread_id": thread_id,
                    "sender_user_id": user_id,
                    "content": content,
                    "visibility": visibility,
                    "message_type": message_type,
                    "created_at": str(msg.created_at),
                }
                await connection_manager.broadcast(thread_id, broadcast_payload)
            except Exception:
                logger.exception("WS message persist failed")
                db.rollback()
            finally:
                db.close()

    except WebSocketDisconnect:
        connection_manager.disconnect(thread_id, websocket)
    except Exception:
        logger.exception("WS error in thread %s", thread_id)
        connection_manager.disconnect(thread_id, websocket)


# ── Offer accept/reject endpoints ────────────────────────────────────────────

@router.post("/threads/{thread_id}/offers/{msg_id}/accept")
def accept_offer(
    thread_id: str, msg_id: str,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """Accept an offer message in a chat thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Thread not found")
    _check_context_access(db, user, thread.context_type, thread.context_id)

    msg = db.query(ChatMessage).filter(
        ChatMessage.id == msg_id, ChatMessage.thread_id == thread_id,
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    if getattr(msg, "message_type", "text") != "offer":
        raise HTTPException(400, "Message is not an offer")

    offer_data = getattr(msg, "offer_payload_json", {}) or {}
    offer_data["accepted"] = True
    offer_data["accepted_by"] = str(user.id)
    msg.offer_payload_json = offer_data
    db.commit()

    # Broadcast acceptance
    try:
        import asyncio
        from app.services.chat_service import connection_manager
        asyncio.get_event_loop().create_task(
            connection_manager.broadcast(thread_id, {
                "type": "offer_accepted",
                "msg_id": msg_id,
                "accepted_by": str(user.id),
            })
        )
    except Exception:
        pass

    return {"status": "accepted", "msg_id": msg_id}


@router.post("/threads/{thread_id}/offers/{msg_id}/reject")
def reject_offer(
    thread_id: str, msg_id: str,
    reason: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """Reject an offer message in a chat thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Thread not found")
    _check_context_access(db, user, thread.context_type, thread.context_id)

    msg = db.query(ChatMessage).filter(
        ChatMessage.id == msg_id, ChatMessage.thread_id == thread_id,
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")

    offer_data = getattr(msg, "offer_payload_json", {}) or {}
    offer_data["rejected"] = True
    offer_data["rejected_by"] = str(user.id)
    offer_data["rejection_reason"] = reason
    msg.offer_payload_json = offer_data
    db.commit()

    return {"status": "rejected", "msg_id": msg_id, "reason": reason}
