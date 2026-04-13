"""
Notification delivery tasks.

References: GAP-007, architecture.md CC-15
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from app.workers import celery_app
except ImportError:
    celery_app = None


def _get_db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _deliver_notification(notification_id: str, *, retries: int = 0, retry_callable=None) -> dict:
    from app.models.notification import Notification
    from app.services.notification_delivery import notification_delivery_service

    db = _get_db()
    try:
        notif = db.query(Notification).filter(Notification.id == notification_id).first()
        if not notif:
            return {"error": "not_found"}
        if notif.delivery_status == "delivered":
            return {"status": "already_delivered"}
        try:
            notification_delivery_service.deliver(db, notif)
            notif.delivery_status = "delivered"
            notif.delivered_at = datetime.now(timezone.utc)
        except Exception as exc:
            notif.retry_count = (notif.retry_count or 0) + 1
            if notif.retry_count >= notif.max_retries:
                notif.delivery_status = "failed"
                logger.warning("Notification %s failed after %d retries", notification_id, notif.max_retries)
            else:
                notif.delivery_status = "pending"
                db.commit()
                if retry_callable:
                    raise retry_callable(exc=exc)
                raise
        db.commit()
        return {"status": notif.delivery_status}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if celery_app:

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
    def task_send_notification(self, notification_id: str) -> dict:
        return _deliver_notification(notification_id, retries=getattr(self.request, "retries", 0), retry_callable=self.retry)


def enqueue_notification_send(notification_id: str) -> None:
    if celery_app:
        task_send_notification.delay(notification_id)
    else:
        _deliver_notification(notification_id)
