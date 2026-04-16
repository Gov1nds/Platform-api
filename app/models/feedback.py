"""
Feedback-loop / continuous-learning models (Phase 3).

References: Execution Plan §9 (Continuous Learning & Feedback Loop),
            migration 016.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class RecommendationOverride(Base):
    """User-initiated override of a system vendor recommendation."""
    __tablename__ = "recommendation_overrides"
    __table_args__ = (
        Index("ix_recommendation_overrides_project", "project_id"),
        Index("ix_recommendation_overrides_recommended_vendor", "recommended_vendor_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    bom_part_id = Column(UUID(as_uuid=False), nullable=True)
    canonical_part_key = Column(Text, nullable=True)
    recommended_vendor_id = Column(UUID(as_uuid=False), nullable=True)
    override_vendor_id = Column(UUID(as_uuid=False), nullable=True)
    override_reason = Column(Text, nullable=True)
    override_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    strategy_at_time = Column(String(40), nullable=True)
    score_at_time = Column(Numeric(6, 4), nullable=True)
    override_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)


class LearningEvent(Base):
    """Append-only audit trail of score adjustments / aliases / demotions."""
    __tablename__ = "learning_events"
    __table_args__ = (
        Index("ix_learning_events_vendor_type", "vendor_id", "event_type"),
        Index("ix_learning_events_review_required", "human_review_required", "created_at"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = Column(String(60), nullable=False)
    vendor_id = Column(UUID(as_uuid=False), nullable=True)
    canonical_part_key = Column(Text, nullable=True)
    trigger = Column(String(60), nullable=False, default="scheduled_recompute")
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    evidence_count_at_time = Column(Integer, nullable=True)
    human_review_required = Column(Boolean, nullable=False, default=False)
    human_review_completed = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
