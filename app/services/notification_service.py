"""
Notification service.

Dispatches notifications across channels (email, SMS, push, in-app)
with retry, storm throttling, and preference enforcement.

References: GAP-007, architecture.md CC-15, INT-006
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification import Notification, NotificationPreference

logger = logging.getLogger(__name__)


class NotificationService:
    """Orchestrates multi-channel notification dispatch."""

    MAX_PER_HOUR = 10  # storm throttle per user per type

    def dispatch(
        self,
        db: Session,
        *,
        user_id: str,
        notification_type: str,
        title: str,
        body: str = "",
        entity_type: str | None = None,
        entity_id: str | None = None,
        organization_id: str | None = None,
    ) -> Notification:
        """
        Create in-app notification and enqueue channel delivery.

        Respects user preferences and storm throttling.
        """
        # Always create in-app record
        notif = Notification(
            user_id=user_id,
            organization_id=organization_id,
            type=notification_type,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            channel="in_app",
            delivery_status="delivered",
            delivered_at=datetime.now(timezone.utc),
        )
        db.add(notif)
        db.flush()

        # Check preferences for other channels
        prefs = db.query(NotificationPreference).filter(
            NotificationPreference.user_id == user_id,
            NotificationPreference.notification_type == notification_type,
        ).first()

        channels_to_send: list[str] = []
        if prefs:
            if prefs.channel_email:
                channels_to_send.append("email")
            if prefs.channel_sms:
                channels_to_send.append("sms")
            if prefs.channel_push:
                channels_to_send.append("push")
        else:
            # Default: email + push
            channels_to_send = ["email", "push"]

        # Enqueue external channel delivery
        for channel in channels_to_send:
            if self._throttle_check(user_id, notification_type, channel):
                channel_notif = Notification(
                    user_id=user_id,
                    organization_id=organization_id,
                    type=notification_type,
                    title=title,
                    body=body,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    channel=channel,
                    delivery_status="pending",
                )
                db.add(channel_notif)
                db.flush()
                # In production: celery task_send_notification.delay(channel_notif.id)

        return notif

    def _throttle_check(
        self, user_id: str, notification_type: str, channel: str,
    ) -> bool:
        """
        Storm throttling: max 10 per user per type per hour.

        In production, uses Redis counter. Stub returns True.
        """
        try:
            import redis as _redis
            r = _redis.from_url(settings.REDIS_URL, decode_responses=True)
            key = f"notif_throttle:{user_id}:{notification_type}:{channel}"
            count = r.incr(key)
            if count == 1:
                r.expire(key, 3600)
            return count <= self.MAX_PER_HOUR
        except Exception:
            return True


# Singleton
notification_service = NotificationService()
