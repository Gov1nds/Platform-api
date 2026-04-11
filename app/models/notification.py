"""
Notification and notification preference models.

References: GAP-007, architecture.md CC-15, INT-006, events.yaml EVT-NOTIF-*
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, Index,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notif_user", "user_id"),
        Index("ix_notif_read", "user_id", "read_at"),
        Index("ix_notif_org", "organization_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=False)  # recipient
    organization_id = Column(UUID(as_uuid=False), nullable=True)
    type = Column(String(80), nullable=False)  # rfq_received, quote_submitted, etc.
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=True)
    entity_type = Column(String(80), nullable=True)
    entity_id = Column(UUID(as_uuid=False), nullable=True)
    channel = Column(String(40), nullable=False, default="in_app")  # email, sms, push, in_app
    delivery_status = Column(String(40), nullable=False, default="pending")  # pending, sent, delivered, failed, bounced
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime(timezone=True), default=_now)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        Index("ix_notifpref_user", "user_id"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), nullable=False)
    notification_type = Column(String(80), nullable=False)
    channel_email = Column(Boolean, nullable=False, default=True)
    channel_sms = Column(Boolean, nullable=False, default=False)
    channel_push = Column(Boolean, nullable=False, default=True)
    channel_in_app = Column(Boolean, nullable=False, default=True)