"""Project participant and access join model."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class ProjectParticipantType(str, enum.Enum):
    owner = "owner"
    collaborator = "collaborator"
    vendor = "vendor"
    approver = "approver"
    watcher = "watcher"


class ProjectParticipantStatus(str, enum.Enum):
    invited = "invited"
    active = "active"
    revoked = "revoked"
    declined = "declined"
    pending = "pending"


class ProjectParticipantAccessLevel(str, enum.Enum):
    read = "read"
    comment = "comment"
    approve = "approve"
    edit = "edit"
    manage = "manage"


class ProjectParticipant(Base):
    __tablename__ = "project_participants"
    __table_args__ = (
        Index("ix_project_participants_project", "project_id"),
        Index("ix_project_participants_user", "user_id"),
        Index("ix_project_participants_vendor", "vendor_id"),
        Index("ix_project_participants_type", "participant_type"),
        Index("ix_project_participants_status", "status"),
        Index("ix_project_participants_approval", "approval_request_id"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=False), ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True)
    invited_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    approval_request_id = Column(UUID(as_uuid=False), ForeignKey("collaboration.approval_requests.id", ondelete="SET NULL"), nullable=True)

    participant_type = Column(Text, nullable=False, default=ProjectParticipantType.collaborator.value)
    access_level = Column(Text, nullable=False, default=ProjectParticipantAccessLevel.read.value)
    status = Column(Text, nullable=False, default=ProjectParticipantStatus.invited.value)
    invited_email = Column(Text, nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="participants")
    user = relationship("User", foreign_keys=[user_id])
    vendor = relationship("Vendor")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    approval_request = relationship("ApprovalRequest", back_populates="participants")
