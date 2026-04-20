"""
Notification, task, alert, and outbox entities.

Contract anchors
----------------
§2.64 Notification              §2.65 Notification_Template
§2.66 Notification_Preference   §2.67 Outbox_Message
§2.68 Task                      §2.69 Alert

State vocabularies
------------------
§3.18 SM-015 Outbox_Message.state  §3.28 Task.status
§3.29 Notification.status          §3.36 Notification.channel
§3.70 Task.task_type               §3.78 Severity

Notes
-----
* ``outbox_message`` drives the transactional outbox pattern — Repo C
  writes a row in the same DB transaction as the business state change,
  and ``notifications.dispatch_outbox_message`` delivers asynchronously.
* ``notification_preference`` has a composite PK (user_id, type, channel).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB as _JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_object,
    tstz,
    uuid_fk,
    uuid_pk,
    uuid_polymorphic,
)
from app.models.enums import (
    NotificationChannel,
    NotificationStatus,
    OutboxMessageState,
    Priority,
    Severity,
    TaskStatus,
    TaskType,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# Notification (§2.64)
# ─────────────────────────────────────────────────────────────────────────────


class Notification(Base, CreatedAtMixin):
    """Outbound notification to a user across a specific channel."""

    __tablename__ = "notification"

    notification_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="CASCADE"
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    action_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime | None] = tstz(nullable=True)
    delivered_at: Mapped[datetime | None] = tstz(nullable=True)
    read_at: Mapped[datetime | None] = tstz(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )

    __table_args__ = (
        enum_check("channel", values_of(NotificationChannel)),
        enum_check("status", values_of(NotificationStatus)),
        UniqueConstraint("idempotency_key", name="uq_notification_idempotency_key"),
        Index(
            "ix_notification_user_id_status_sent_at",
            "user_id",
            "status",
            "sent_at",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NotificationTemplate (§2.65)
# ─────────────────────────────────────────────────────────────────────────────


class NotificationTemplate(Base, CreatedAtMixin):
    """Per-locale Jinja template for a notification type + channel."""

    __tablename__ = "notification_template"

    template_id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    subject_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        enum_check("channel", values_of(NotificationChannel)),
        UniqueConstraint("key", name="uq_notification_template_key"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NotificationPreference (§2.66) — composite PK
# ─────────────────────────────────────────────────────────────────────────────


class NotificationPreference(Base):
    """Per (user × type × channel) opt-in flag."""

    __tablename__ = "notification_preference"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    type: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(8), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    __table_args__ = (
        enum_check("channel", values_of(NotificationChannel)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OutboxMessage (§2.67)  — transactional outbox pattern
# ─────────────────────────────────────────────────────────────────────────────


class OutboxMessage(Base, CreatedAtMixin):
    """Transactional outbox row — written atomically with the originating
    state change, dispatched by ``notifications.dispatch_outbox_message``."""

    __tablename__ = "outbox_message"

    outbox_id: Mapped[uuid.UUID] = uuid_pk()
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = uuid_polymorphic()
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # payload_json is NOT NULL and supplied by the caller at insert time
    # (no server default — every outbox write must carry a payload).
    payload_json: Mapped[dict] = mapped_column(
        _JSONB,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_retry_at: Mapped[datetime | None] = tstz(nullable=True)
    delivered_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("state", values_of(OutboxMessageState)),
        CheckConstraint("retry_count >= 0", name="retry_count_nonneg"),
        Index("ix_outbox_message_state_next_retry_at", "state", "next_retry_at"),
        Index(
            "ix_outbox_message_aggregate_type_aggregate_id",
            "aggregate_type",
            "aggregate_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task (§2.68)
# ─────────────────────────────────────────────────────────────────────────────


class Task(Base, CreatedAtMixin):
    """Action-queue task assigned to a user — capped at 20 per user per
    §26/§27 attention queue."""

    __tablename__ = "task"

    task_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="CASCADE"
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NORMAL'")
    )
    due_date: Mapped[datetime | None] = tstz(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )

    __table_args__ = (
        enum_check("task_type", values_of(TaskType)),
        enum_check("priority", values_of(Priority)),
        enum_check("status", values_of(TaskStatus)),
        Index(
            "ix_task_user_id_status_priority",
            "user_id",
            "status",
            "priority",
        ),
        Index("ix_task_entity_type_entity_id", "entity_type", "entity_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Alert (§2.69)
# ─────────────────────────────────────────────────────────────────────────────


class Alert(Base, CreatedAtMixin):
    """Admin / operational alert surfaced by monitors and background jobs."""

    __tablename__ = "alert"

    alert_id: Mapped[uuid.UUID] = uuid_pk()
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("severity", values_of(Severity)),
        Index("ix_alert_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_alert_severity_created_at", "severity", "created_at"),
        Index("ix_alert_alert_type_created_at", "alert_type", "created_at"),
    )


__all__ = [
    "Notification",
    "NotificationTemplate",
    "NotificationPreference",
    "OutboxMessage",
    "Task",
    "Alert",
]
