"""
Notification management routes.

Endpoints:
  GET    /notifications              -- List notifications (paginated, filterable)
  PATCH  /notifications/{nid}/read   -- Mark single notification as read
  POST   /notifications/mark-all-read -- Mark all notifications as read

References: GAP-007, architecture.md CC-15
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.utils.dependencies import require_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
def list_notifications(
    read: bool | None = Query(None, description="Filter by read status"),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List notifications for the current user."""
    q = db.query(Notification).filter(Notification.user_id == user.id)

    if read is True:
        q = q.filter(Notification.read_at.isnot(None))
    elif read is False:
        q = q.filter(Notification.read_at.is_(None))

    total = q.count()

    if cursor:
        q = q.filter(Notification.id < cursor)

    items = q.order_by(Notification.created_at.desc()).limit(limit).all()
    next_cursor = items[-1].id if len(items) == limit else None

    return PaginatedResponse(
        items=[
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "entity_type": n.entity_type,
                "entity_id": n.entity_id,
                "channel": n.channel,
                "delivery_status": n.delivery_status,
                "read_at": str(n.read_at) if n.read_at else None,
                "created_at": str(n.created_at) if n.created_at else None,
            }
            for n in items
        ],
        next_cursor=next_cursor,
        total_count=total,
    )


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Mark a single notification as read."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user.id,
    ).first()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "id": notif.id}


@router.post("/mark-all-read")
def mark_all_read(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Mark all unread notifications as read."""
    now = datetime.now(timezone.utc)
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
        .update({"read_at": now}, synchronize_session="fetch")
    )
    db.commit()
    return {"status": "ok", "marked": count}
