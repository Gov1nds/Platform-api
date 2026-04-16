"""
Phase 3 market intelligence models.

References: Execution Plan §4 (Market Intelligence Integration),
            migration 015.

Adds the Phase 3 market signal tables while leaving the existing
app.models.market module (FXRate, FreightRate, TariffSchedule,
CommodityIndex, etc.) unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class CommodityPriceSignal(Base):
    """
    Commodity / raw-material price signal with trend classification.

    is_valley is set True when the price is at a local minimum inside a
    90-day rolling window — implements the "buy in the valley" guidance
    from Execution Plan §4.
    """
    __tablename__ = "commodity_price_signals"
    __table_args__ = (
        Index("ix_commodity_price_signals_family_date", "material_family_tag", "price_date"),
        Index("ix_commodity_price_signals_commodity_date", "commodity_name", "price_date"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    commodity_name = Column(Text, nullable=False)
    material_family_tag = Column(Text, nullable=True)
    price_per_unit = Column(Numeric(20, 8), nullable=False)
    unit = Column(String(20), nullable=False, default="kg")
    currency = Column(String(3), nullable=False, default="USD")
    price_date = Column(Date, nullable=False)
    source = Column(String(80), nullable=False, default="seed")
    trend_direction = Column(String(10), nullable=True)  # rising/falling/stable
    trend_pct_30d = Column(Numeric(8, 4), nullable=True)
    is_valley = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)


class VendorLeadTimeHistoryPhase3(Base):
    """
    Actual-vs-quoted lead-time observations per vendor+category.

    Feeds the Lead-Time Intelligence service (§4) for distribution
    calculations (mean, p90, etc.) and reliability scoring.

    NOTE: Uses table name pricing.vendor_lead_time_history which is
    distinct from the Phase-2c pricing.lead_time_history table in
    app.models.outcomes. The class name is suffixed with _Phase3 to
    avoid mapper collision with LeadTimeHistory.
    """
    __tablename__ = "vendor_lead_time_history"
    __table_args__ = (
        Index("ix_vendor_lt_history_p3_vendor_cat", "vendor_id", "category_tag"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_tag = Column(Text, nullable=True)
    material_family = Column(Text, nullable=True)
    actual_lead_time_days = Column(Numeric(12, 2), nullable=True)
    quoted_lead_time_days = Column(Numeric(12, 2), nullable=True)
    deviation_days = Column(Numeric(12, 2), nullable=True)
    source_rfq_id = Column(UUID(as_uuid=False), nullable=True)
    source_po_id = Column(UUID(as_uuid=False), nullable=True)
    recorded_at = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)


class MarketAnomalyEvent(Base):
    """
    Anomaly / outlier event flagged by the market anomaly service.

    Types: zero_lead_time, near_zero_price, price_spike, lead_time_spike,
    impossible_moq, stockout_surge. Severity: LOW/MEDIUM/HIGH/CRITICAL.
    """
    __tablename__ = "market_anomaly_events"
    __table_args__ = (
        Index("ix_market_anomaly_events_vendor_type", "vendor_id", "anomaly_type"),
        Index("ix_market_anomaly_events_severity", "severity", "detected_at"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
        nullable=True,
    )
    canonical_part_key = Column(Text, nullable=True)
    anomaly_type = Column(String(40), nullable=False)
    observed_value = Column(Numeric(20, 8), nullable=True)
    expected_range_low = Column(Numeric(20, 8), nullable=True)
    expected_range_high = Column(Numeric(20, 8), nullable=True)
    severity = Column(String(20), nullable=False, default="MEDIUM")
    auto_flagged = Column(Boolean, nullable=False, default=True)
    reviewed = Column(Boolean, nullable=False, default=False)
    review_outcome = Column(String(40), nullable=True)
    event_metadata = Column(JSONB, nullable=False, default=dict)
    detected_at = Column(DateTime(timezone=True), default=_now)
    created_at = Column(DateTime(timezone=True), default=_now)


class RegionalStrategyRun(Base):
    """Audit row for each per-project regional sourcing strategy evaluation."""
    __tablename__ = "regional_strategy_runs"
    __table_args__ = (
        Index("ix_regional_strategy_runs_project", "project_id"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    requester_location = Column(JSONB, nullable=False, default=dict)
    local_bucket = Column(Text, nullable=True)
    regional_bucket = Column(JSONB, nullable=False, default=list)
    national_bucket = Column(Text, nullable=True)
    international_bucket = Column(Text, nullable=True)
    strategy_results = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
