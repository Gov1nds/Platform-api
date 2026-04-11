"""
Market data models: FX rates, freight rates, tariffs, commodity indices,
and integration run logs.

References: GAP-022, GAP-003, architecture.md Domain 8, DG-003
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, DateTime, Numeric, Integer, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class FXRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        Index("ix_fx_pair", "base_currency", "quote_currency"),
        Index("ix_fx_freshness", "freshness_status"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    base_currency = Column(String(3), nullable=False)
    quote_currency = Column(String(3), nullable=False)
    rate = Column(Numeric(20, 8), nullable=False)
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=1.0)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), default=_now)

    # Freshness tracking (DG-003)
    freshness_status = Column(String(20), nullable=False, default="FRESH")  # FreshnessStatus
    ttl_seconds = Column(Integer, nullable=False, default=900)  # 15 min
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    provider_id = Column(String(80), nullable=True)
    data_source = Column(String(120), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)


class FreightRate(Base):
    __tablename__ = "freight_rates"
    __table_args__ = (
        Index("ix_freight_route", "origin_region", "destination_region"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    origin_region = Column(Text, nullable=False)
    destination_region = Column(Text, nullable=False)
    mode = Column(Text, nullable=False, default="sea")
    rate_per_kg = Column(Numeric(20, 8), nullable=True)
    rate_per_cbm = Column(Numeric(20, 8), nullable=True)
    min_charge = Column(Numeric(20, 8), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    transit_days = Column(Numeric(12, 2), nullable=True)
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0.7)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)

    # Freshness tracking
    freshness_status = Column(String(20), nullable=False, default="FRESH")
    ttl_seconds = Column(Integer, nullable=False, default=3600)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    provider_id = Column(String(80), nullable=True)
    data_source = Column(String(120), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)


class TariffSchedule(Base):
    __tablename__ = "tariff_schedules"
    __table_args__ = (
        Index("ix_tariff_hs", "hs_code"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    hs_code = Column(String(20), nullable=False)
    origin_country = Column(String(3), nullable=False)
    destination_country = Column(String(3), nullable=False)
    duty_rate_pct = Column(Numeric(8, 4), nullable=False, default=0)
    additional_taxes_pct = Column(Numeric(8, 4), nullable=False, default=0)
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0.6)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)

    # Freshness tracking
    freshness_status = Column(String(20), nullable=False, default="FRESH")
    ttl_seconds = Column(Integer, nullable=False, default=604800)  # 7 days
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    provider_id = Column(String(80), nullable=True)
    data_source = Column(String(120), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)


class CommodityIndex(Base):
    __tablename__ = "commodity_indices"
    __table_args__ = (
        Index("ix_commodity_name", "commodity_name"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    commodity_name = Column(Text, nullable=False)
    unit = Column(Text, nullable=False, default="kg")
    price = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0.7)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)

    # Freshness tracking
    freshness_status = Column(String(20), nullable=False, default="FRESH")
    ttl_seconds = Column(Integer, nullable=False, default=3600)  # 1 hour
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    provider_id = Column(String(80), nullable=True)
    data_source = Column(String(120), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)


class IntegrationRunLog(Base):
    """Logs every external integration call for observability and debugging."""
    __tablename__ = "integration_run_logs"
    __table_args__ = (
        Index("ix_irl_integration", "integration_id"),
        Index("ix_irl_provider", "provider"),
        Index("ix_irl_created", "created_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    integration_id = Column(String(80), nullable=False)  # INT-001, INT-002, etc.
    provider = Column(String(80), nullable=False)  # digikey, mouser, etc.
    operation = Column(String(80), nullable=False)  # fetch_rates, fetch_pricing, etc.
    status = Column(String(40), nullable=False)  # success, failed, timeout, circuit_open
    latency_ms = Column(Integer, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=1)
    error_message = Column(Text, nullable=True)
    request_payload_hash = Column(String(128), nullable=True)
    response_record_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)