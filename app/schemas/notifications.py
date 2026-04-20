"""
notifications.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Notifications, Tasks & Alert Schema Layer

CONTRACT AUTHORITY: contract.md §2.64–2.69 (Notification, NotificationTemplate,
NotificationPreference, OutboxMessage, Task, Alert), §3.18 (SM-015
Outbox_Message.state), §3.28–3.29 (Task/Notification status), §4.11
(Notifications & Tasks endpoints).

Invariants:
  • All outbound notifications go through the notification engine — no direct
    SMTP/SMS/push from feature code (requirements.yaml invariant).
  • Idempotency keys prevent duplicate dispatch on retry (outbox pattern).
  • Storm throttling: max N notifications per user per hour (configurable).
  • Outbox_Message SM-015: PENDING → IN_FLIGHT → DELIVERED | FAILED →
    [PENDING retry] → DEAD_LETTERED (max retries exceeded).
  • Task.status: OPEN | IN_PROGRESS | RESOLVED | DISMISSED (§3.28).
  • Notification.status: PENDING | SENT | DELIVERED | FAILED | READ (§3.29).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import (
    NotificationChannel,
    NotificationStatus,
    OutboxMessageState,
    PGIBase,
    Priority,
    Severity,
    TaskStatus,
    TaskType,
)


# ──────────────────────────────────────────────────────────────────────────
# Notification (contract §2.64)
# ──────────────────────────────────────────────────────────────────────────

class NotificationSchema(PGIBase):
    """A dispatched notification to a user via a specific channel.

    idempotency_key: UNIQUE — prevents duplicate dispatch on retry.
    action_url: deep link in the product the user should navigate to.
    """

    notification_id: UUID
    user_id: UUID
    type: str = Field(max_length=64)
    channel: NotificationChannel
    title: str = Field(max_length=255)
    body: str
    action_url: Optional[str] = Field(default=None, max_length=1024)
    idempotency_key: str = Field(max_length=128)
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    status: NotificationStatus


class NotificationListResponse(PGIBase):
    """Cursor-paginated notification list (GET /api/v1/notifications)."""

    items: list[NotificationSchema]
    next_cursor: Optional[str] = None


class MarkNotificationReadResponse(PGIBase):
    """Response after marking a notification as read."""

    notification_id: UUID
    read_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Notification_Template (contract §2.65)
# ──────────────────────────────────────────────────────────────────────────

class NotificationTemplateSchema(PGIBase):
    """Jinja2-rendered notification template keyed by type and channel.

    key: globally unique string identifier (e.g. 'rfq.sent', 'po.approved').
    subject_template: used for email; null for SMS/push/in_app.
    locale: language code (e.g. 'en-US', 'fr-FR').
    """

    template_id: UUID
    key: str = Field(max_length=128)
    channel: NotificationChannel
    subject_template: Optional[str] = None
    body_template: str
    locale: str = Field(max_length=16)


# ──────────────────────────────────────────────────────────────────────────
# Notification_Preference (contract §2.66)
# ──────────────────────────────────────────────────────────────────────────

class NotificationPreferenceSchema(PGIBase):
    """Per-user, per-type, per-channel notification preference.

    Composite PK: (user_id, type, channel).
    enabled = False suppresses dispatch for this combination.
    """

    user_id: UUID
    type: str = Field(max_length=64)
    channel: NotificationChannel
    enabled: bool = True


class NotificationPreferenceUpdateRequest(PGIBase):
    """Enable or disable a specific notification type+channel combination."""

    enabled: bool


class NotificationPreferenceListResponse(PGIBase):
    """All preferences for a user."""

    user_id: UUID
    preferences: list[NotificationPreferenceSchema]


# ──────────────────────────────────────────────────────────────────────────
# Outbox_Message (contract §2.67)
# ──────────────────────────────────────────────────────────────────────────

class OutboxMessageSchema(PGIBase):
    """Transactional outbox message (SM-015).

    Guarantees at-least-once delivery for all business events.
    Consumers must tolerate duplicate delivery.
    state: PENDING → IN_FLIGHT → DELIVERED | FAILED → DEAD_LETTERED.
    """

    outbox_id: UUID
    aggregate_type: str = Field(max_length=64)
    aggregate_id: UUID
    event_type: str = Field(max_length=128)
    payload_json: dict[str, Any]
    state: OutboxMessageState
    retry_count: int = 0
    next_retry_at: Optional[datetime] = None
    created_at: datetime
    delivered_at: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────
# Task (contract §2.68)
# ──────────────────────────────────────────────────────────────────────────

class TaskSchema(PGIBase):
    """An actionable task in a user's attention queue.

    task_type: review_normalization | approve_po | confirm_gr |
               respond_to_rfq | other.
    priority: LOW | NORMAL | HIGH | URGENT.
    Capped at 20 items per attention queue (contract §4.11 note).
    """

    task_id: UUID
    user_id: UUID
    entity_type: str = Field(max_length=32)
    entity_id: UUID
    task_type: TaskType
    priority: Priority = Field(description="LOW | NORMAL | HIGH | URGENT.")
    due_date: Optional[datetime] = None
    status: TaskStatus
    created_at: datetime


class TaskListResponse(PGIBase):
    """Cursor-paginated task list (GET /api/v1/tasks).

    Capped at 20 items per query per LAW-3 / §4.11.
    """

    items: list[TaskSchema]
    next_cursor: Optional[str] = None


class TaskCompleteRequest(PGIBase):
    """Mark a task as resolved (POST /api/v1/tasks/{id}/complete)."""

    notes: Optional[str] = Field(default=None, max_length=2000)


class TaskCompleteResponse(PGIBase):
    """Response after completing a task."""

    task_id: UUID
    status: TaskStatus  # Always RESOLVED


# ──────────────────────────────────────────────────────────────────────────
# Alert (contract §2.69)
# ──────────────────────────────────────────────────────────────────────────

class AlertSchema(PGIBase):
    """An operational alert surfaced to operators or buyers.

    Created by: SLA monitor, stale-tracking detector, market data refresh failures.
    severity: LOW | MEDIUM | HIGH | CRITICAL.
    resolved_at: null when still active.
    """

    alert_id: UUID
    alert_type: str = Field(max_length=32)
    severity: Severity
    entity_type: str = Field(max_length=32)
    entity_id: UUID
    message: str
    resolved_at: Optional[datetime] = None
    created_at: datetime


class AlertCreateRequest(PGIBase):
    """Internal schema for creating an alert (used by worker processes)."""

    alert_type: str = Field(min_length=1, max_length=32)
    severity: Severity
    entity_type: str = Field(min_length=1, max_length=32)
    entity_id: UUID
    message: str = Field(min_length=1, max_length=1000)
