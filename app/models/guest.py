"""
Guest / pre-auth entities.

Contract anchors
----------------
§2.70 Guest_Session           §2.71 Guest_Search_Log
§2.72 Guest_Report_Snapshot   §2.73 Guest_Rate_Limit_Bucket

State vocabularies
------------------
§3.20 SM-003 Guest_Session.state   §3.86 Guest_Rate_Limit_Bucket.scope

Conflict notes
--------------
* CN-1 / CN-14: ``guest_session`` cookie TTL is 30-day sliding;
  ``guest_search_log`` retention is 90 days before anonymization.
  Cookie TTL is enforced via ``expires_at`` + the daily
  ``platform.archive_stale_guest_sessions`` job.
* ``guest_rate_limit_bucket`` is primarily implemented in Redis; this
  table is a fallback persistence for abuse analysis (§2.73).
* All vendor results captured in ``vendor_results_json`` must be
  contact-redacted before insert (service responsibility).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    currency_code_nullable,
    enum_check,
    jsonb_array,
    jsonb_object,
    jsonb_object_nullable,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    GuestRateLimitScope,
    GuestSessionState,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# GuestSession (§2.70)
# ─────────────────────────────────────────────────────────────────────────────


class GuestSession(Base):
    """Anonymous guest session carrying a signed httpOnly cookie token.

    Lifecycle: NEW → ACTIVE → (CONVERTED | EXPIRED). 30-day sliding TTL
    (CN-14).
    """

    __tablename__ = "guest_session"

    session_id: Mapped[uuid.UUID] = uuid_pk()
    session_token: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NEW'")
    )
    detected_location_json: Mapped[dict] = jsonb_object()
    detected_currency: Mapped[str | None] = currency_code_nullable()
    overridden_location_json: Mapped[dict | None] = jsonb_object_nullable()
    overridden_currency: Mapped[str | None] = currency_code_nullable()
    component_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    converted_to_user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True
    )
    created_at: Mapped[datetime] = tstz(default_now=True)
    last_active_at: Mapped[datetime] = tstz(default_now=True)
    expires_at: Mapped[datetime] = tstz()

    __table_args__ = (
        enum_check("state", values_of(GuestSessionState)),
        CheckConstraint(
            "state = 'CONVERTED' OR converted_to_user_id IS NULL",
            name="guest_session_converted_user_requires_converted_state",
        ),
        UniqueConstraint("session_token", name="uq_guest_session_session_token"),
        Index("ix_guest_session_state_expires_at", "state", "expires_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GuestSearchLog (§2.71)
# ─────────────────────────────────────────────────────────────────────────────


class GuestSearchLog(Base, CreatedAtMixin):
    """One guest search event — retained 90 days before anonymization (CN-14)."""

    __tablename__ = "guest_search_log"

    search_id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = uuid_fk(
        "guest_session.session_id", ondelete="CASCADE"
    )
    search_query: Mapped[str] = mapped_column(Text, nullable=False)
    components_json: Mapped[list] = jsonb_array()
    detected_location_json: Mapped[dict] = jsonb_object()
    detected_currency: Mapped[str | None] = currency_code_nullable()
    # Contact-redacted by guest_service before insert.
    vendor_results_json: Mapped[list] = jsonb_array()
    free_report_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    converted_to_signup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index(
            "ix_guest_search_log_session_id_created_at",
            "session_id",
            "created_at",
        ),
        Index(
            "ix_guest_search_log_free_report_generated",
            "free_report_generated",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GuestReportSnapshot (§2.72)
# ─────────────────────────────────────────────────────────────────────────────


class GuestReportSnapshot(Base, CreatedAtMixin):
    """Immutable snapshot of the free report shown to a guest — preserves
    the exact content generated at query time for conversion analytics."""

    __tablename__ = "guest_report_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    search_id: Mapped[uuid.UUID] = uuid_fk(
        "guest_search_log.search_id", ondelete="CASCADE"
    )
    report_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index(
            "ix_guest_report_snapshot_search_id",
            "search_id",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GuestRateLimitBucket (§2.73)
# ─────────────────────────────────────────────────────────────────────────────


class GuestRateLimitBucket(Base, CreatedAtMixin):
    """Fallback rate-limit persistence (primary impl is Redis).

    Retained for abuse analysis after Redis entries expire.
    """

    __tablename__ = "guest_rate_limit_bucket"

    bucket_id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    window_start: Mapped[datetime] = tstz(default_now=True)
    window_end: Mapped[datetime] = tstz()

    __table_args__ = (
        enum_check("scope", values_of(GuestRateLimitScope)),
        UniqueConstraint(
            "scope",
            "identifier",
            "window_start",
            name="uq_guest_rate_limit_scope_id_window",
        ),
    )


__all__ = [
    "GuestSession",
    "GuestSearchLog",
    "GuestReportSnapshot",
    "GuestRateLimitBucket",
]
