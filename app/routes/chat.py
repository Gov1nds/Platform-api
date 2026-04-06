from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.chat import ChatThread, ChatMessage
from app.models.project import Project
from app.models.rfq import RFQBatch
from app.schemas import ThreadCreateRequest, MessageCreateRequest, ThreadResponse, MessageResponse
from app.utils.dependencies import require_user

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
                 user:User=Depends(require_user), db:Session=Depends(get_db)):
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread: raise HTTPException(404)
    _check_context_access(db, user, thread.context_type, thread.context_id)
    q = db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id)
    if visibility: q = q.filter(ChatMessage.visibility == visibility)
    return [MessageResponse.model_validate(m) for m in q.order_by(ChatMessage.created_at.asc()).all()]
