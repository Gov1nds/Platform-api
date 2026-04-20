"""
Approval / dispute / exception case entities.

Contract anchors
----------------
§2.55 Approval_Request    §2.56 Approval_Decision (APPEND-ONLY)
§2.57 Dispute             §2.58 Exception_Case

State vocabularies
------------------
§3.14 SM-011 Approval_Request.status   §3.26 Dispute.status
§3.27 Exception_Case.status            §3.67 Approval_Request.entity_type
§3.68 Approval_Decision.decision       §3.69 Dispute.entity_type
§3.77 Exception_Case.exception_type    §3.78 Severity

Notes
-----
* ``entity_id`` on Approval_Request / Dispute / Exception_Case is a
  polymorphic pointer — FK is not enforced at the DB layer because the
  target table depends on ``entity_type``. Integrity is enforced in the
  service layer (application + audit).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
    uuid_polymorphic,
)
from app.models.enums import (
    ApprovalDecisionValue,
    ApprovalRequestEntityType,
    ApprovalRequestStatus,
    DisputeEntityType,
    DisputeStatus,
    ExceptionCaseStatus,
    ExceptionType,
    Severity,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# ApprovalRequest (§2.55)
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalRequest(Base, CreatedAtMixin):
    """Approval gate for POs, invoices, and change orders over threshold."""

    __tablename__ = "approval_request"

    approval_id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    requested_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    assigned_to: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    threshold_amount: Mapped = money_default_zero()
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    deadline: Mapped[datetime] = tstz()
    decided_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("entity_type", values_of(ApprovalRequestEntityType)),
        enum_check("status", values_of(ApprovalRequestStatus)),
        Index("ix_approval_request_assigned_to_status", "assigned_to", "status"),
        Index(
            "ix_approval_request_entity_type_entity_id",
            "entity_type",
            "entity_id",
        ),
        Index("ix_approval_request_deadline", "deadline"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ApprovalDecision (§2.56)  — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalDecision(Base, CreatedAtMixin):
    """Append-only decision log for Approval_Request."""

    __tablename__ = "approval_decision"

    decision_id: Mapped[uuid.UUID] = uuid_pk()
    approval_id: Mapped[uuid.UUID] = uuid_fk(
        "approval_request.approval_id", ondelete="RESTRICT"
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("decision", values_of(ApprovalDecisionValue)),
        Index("ix_approval_decision_approval_id", "approval_id"),
        Index("ix_approval_decision_decided_by", "decided_by"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dispute (§2.57)
# ─────────────────────────────────────────────────────────────────────────────


class Dispute(Base, CreatedAtMixin):
    """Buyer/vendor dispute against an invoice, PO, or shipment."""

    __tablename__ = "dispute"

    dispute_id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    raised_by: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = tstz(default_now=True)
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("entity_type", values_of(DisputeEntityType)),
        enum_check("status", values_of(DisputeStatus)),
        Index("ix_dispute_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_dispute_status", "status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ExceptionCase (§2.58)
# ─────────────────────────────────────────────────────────────────────────────


class ExceptionCase(Base, CreatedAtMixin):
    """Generic exception case (SLA breach, stale tracking, etc.) with
    severity + assignment."""

    __tablename__ = "exception_case"

    case_id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = uuid_polymorphic()
    exception_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'MEDIUM'")
    )
    assigned_to: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("exception_type", values_of(ExceptionType)),
        enum_check("severity", values_of(Severity)),
        enum_check("status", values_of(ExceptionCaseStatus)),
        Index("ix_exception_case_status_severity", "status", "severity"),
        Index(
            "ix_exception_case_entity_type_entity_id",
            "entity_type",
            "entity_id",
        ),
    )


__all__ = [
    "ApprovalRequest",
    "ApprovalDecision",
    "Dispute",
    "ExceptionCase",
]
