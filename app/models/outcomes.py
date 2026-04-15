from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base



def _now() -> datetime:
    return datetime.now(timezone.utc)



def _uuid() -> str:
    return str(uuid.uuid4())


class QuoteOutcome(Base):
    """Append-only quote and order outcome observations per BOM line / vendor."""

    __tablename__ = "quote_outcomes"
    __table_args__ = (
        Index("ix_quote_outcomes_bom_line_vendor", "bom_line_id", "vendor_id"),
        Index("ix_quote_outcomes_vendor_date", "vendor_id", "quote_date"),
        Index("ix_quote_outcomes_accepted", "is_accepted"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    quoted_price = Column(Numeric(20, 8), nullable=True)
    quoted_lead_time = Column(Numeric(12, 2), nullable=True)
    is_accepted = Column(Boolean, nullable=False, default=False)
    accepted_price = Column(Numeric(20, 8), nullable=True)
    accepted_lead_time = Column(Numeric(12, 2), nullable=True)
    quote_date = Column(Date, nullable=True)
    order_date = Column(Date, nullable=True)
    delivery_date = Column(Date, nullable=True)
    issues_flag = Column(Boolean, nullable=False, default=False)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    bom_line = relationship("BOMPart")
    vendor = relationship("Vendor")


class OverrideEvent(Base):
    """Append-only manual override audit log for recommendation divergence."""

    __tablename__ = "override_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_override_events_event_id"),
        Index("ix_override_events_bom_line_timestamp", "bom_line_id", "timestamp"),
        Index("ix_override_events_user_timestamp", "user_id", "timestamp"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_id = Column(String(128), nullable=False)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("auth.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    bom_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommended_vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    chosen_vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    override_reason_code = Column(String(80), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_now)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    user = relationship("User")
    bom_line = relationship("BOMPart")
    recommended_vendor = relationship("Vendor", foreign_keys=[recommended_vendor_id])
    chosen_vendor = relationship("Vendor", foreign_keys=[chosen_vendor_id])


class LeadTimeHistory(Base):
    """Append-only completed-order lead time observations derived from quote outcomes."""

    __tablename__ = "lead_time_history"
    __table_args__ = (
        UniqueConstraint("quote_outcome_id", name="uq_lead_time_history_quote_outcome"),
        Index("ix_lead_time_history_vendor_recorded", "vendor_id", "recorded_at"),
        Index("ix_lead_time_history_vendor_bom", "vendor_id", "bom_line_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    quote_outcome_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.quote_outcomes.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    bom_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    quoted_lead_time = Column(Numeric(12, 2), nullable=True)
    actual_lead_time = Column(Numeric(12, 2), nullable=False)
    lead_time_diff_days = Column(Numeric(12, 2), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    quote_outcome = relationship("QuoteOutcome")
    vendor = relationship("Vendor")
    bom_line = relationship("BOMPart")


class VendorPerformance(Base):
    """Aggregated vendor scorecard foundation rows for a reporting period."""

    __tablename__ = "vendor_performance"
    __table_args__ = (
        UniqueConstraint("vendor_id", "period_start", "period_end", name="uq_vendor_performance_period"),
        Index("ix_vendor_performance_vendor_period", "vendor_id", "period_start", "period_end"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    on_time_rate = Column(Numeric(12, 6), nullable=True)
    avg_lead_time = Column(Numeric(12, 2), nullable=True)
    lead_time_variance = Column(Numeric(12, 4), nullable=True)
    price_variance = Column(Numeric(20, 8), nullable=True)
    po_win_rate = Column(Numeric(12, 6), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    vendor = relationship("Vendor")

class AnomalyFlag(Base):
    """
    Append-only deterministic anomaly flags for pricing, lead-time, and
    availability signals.

    Rows do not mutate source data. Duplicate spam is controlled through a
    deterministic dedupe window key.
    """

    __tablename__ = "anomaly_flags"
    __table_args__ = (
        UniqueConstraint("anomaly_id", name="uq_anomaly_flags_anomaly_id"),
        UniqueConstraint("dedupe_window_key", name="uq_anomaly_flags_dedupe_window_key"),
        Index("ix_anomaly_flags_entity", "entity_type", "entity_id"),
        Index("ix_anomaly_flags_metric_detected", "metric_name", "detected_at"),
        Index("ix_anomaly_flags_severity_detected", "severity", "detected_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    anomaly_id = Column(String(128), nullable=False, default=lambda: f"anomaly-{uuid.uuid4().hex}")
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(128), nullable=False)
    metric_name = Column(String(80), nullable=False)
    observed_value = Column(Numeric(20, 8), nullable=True)
    threshold_value = Column(Numeric(20, 8), nullable=True)
    anomaly_type = Column(String(80), nullable=False)
    severity = Column(String(20), nullable=False, default="medium")
    detected_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    source_context_json = Column(JSONB, nullable=False, default=dict)
    dedupe_window_key = Column(String(180), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
