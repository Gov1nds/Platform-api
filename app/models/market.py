import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, Numeric, Index
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
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    base_currency = Column(String(3), nullable=False)
    quote_currency = Column(String(3), nullable=False)
    rate = Column(Numeric(18, 8), nullable=False)
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=1.0)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), default=_now)
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
    rate_per_kg = Column(Numeric(18, 6), nullable=True)
    rate_per_cbm = Column(Numeric(18, 6), nullable=True)
    min_charge = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    transit_days = Column(Numeric(12, 2), nullable=True)
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0.7)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)
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
    price = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    source = Column(Text, nullable=True)
    confidence = Column(Numeric(6, 4), nullable=False, default=0.7)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
