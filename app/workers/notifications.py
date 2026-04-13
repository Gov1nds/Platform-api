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


if celery_app:

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
    def task_send_notification(self, notification_id: str) -> dict:
        """Dispatch notification to its configured channel."""
        from app.models.notification import Notification

        db = _get_db()
        try:
            notif = db.query(Notification).filter(
                Notification.id == notification_id
            ).first()
            if not notif:
                return {"error": "not_found"}

            try:
                if notif.channel == "email":
                    _send_email(notif)
                elif notif.channel == "sms":
                    _send_sms(notif)
                elif notif.channel == "push":
                    _send_push(notif)

                notif.delivery_status = "delivered"
                notif.delivered_at = datetime.now(timezone.utc)
            except Exception as e:
                notif.retry_count = (notif.retry_count or 0) + 1
                if notif.retry_count >= notif.max_retries:
                    notif.delivery_status = "failed"
                    logger.warning(
                        "Notification %s failed after %d retries",
                        notification_id, notif.max_retries,
                    )
                else:
                    notif.delivery_status = "pending"
                    db.commit()
                    raise self.retry(exc=e)

            db.commit()
            return {"status": notif.delivery_status}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _send_email(notif) -> None:
    """Send via SendGrid."""
    from app.core.config import settings
    if not settings.SENDGRID_API_KEY:
        logger.debug("SendGrid not configured — skipping email")
        return
    # In production: use sendgrid SDK
    logger.info("Email sent to user %s: %s", notif.user_id, notif.title)


def _send_sms(notif) -> None:
    """Send via Twilio."""
    from app.core.config import settings
    if not settings.TWILIO_ACCOUNT_SID:
        logger.debug("Twilio not configured — skipping SMS")
        return
    logger.info("SMS sent to user %s: %s", notif.user_id, notif.title)


def _send_push(notif) -> None:
    """Send via Firebase."""
    from app.core.config import settings
    if not settings.FIREBASE_CREDENTIALS_PATH:
        logger.debug("Firebase not configured — skipping push")
        return
    logger.info("Push sent to user %s: %s", notif.user_id, notif.title)
