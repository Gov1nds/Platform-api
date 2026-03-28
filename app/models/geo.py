"""Geo models — maps to geo.countries, geo.region_profiles, geo.exchange_rates.

Replaces hardcoded REGION_PROFILES/FOREX_RATES in strategy_service.py
with DB-backed data. Seeded at startup, queryable at runtime.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, Numeric, Boolean, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base


class Country(Base):
    __tablename__ = "countries"
    __table_args__ = (
        Index("ix_countries_code", "code"),
        {"schema": "geo"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(Text, nullable=False, unique=True)  # ISO 3166 or custom
    name = Column(Text, nullable=False)
    region_name = Column(Text, nullable=True)  # e.g. "India", "EU (Germany)"
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class RegionProfile(Base):
    """Manufacturing/logistics profile per region.
    Mirrors REGION_PROFILES dict structure but stored in DB."""
    __tablename__ = "region_profiles"
    __table_args__ = (
        Index("ix_region_profiles_name", "region_name"),
        {"schema": "geo"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    region_name = Column(Text, nullable=False, unique=True)
    base_cost_mult = Column(Numeric(6, 4), nullable=False, default=1.0)
    labor_rate_hr = Column(Numeric(10, 2), nullable=False, default=50)
    lead_days_base = Column(Integer, nullable=False, default=14)
    logistics_per_kg = Column(Numeric(8, 2), nullable=False, default=3.0)
    tariff_pct = Column(Numeric(6, 4), nullable=False, default=0.0)
    risk_base = Column(Numeric(6, 4), nullable=False, default=0.10)
    quality_score = Column(Numeric(6, 4), nullable=False, default=0.80)
    moq_threshold = Column(Integer, nullable=False, default=50)
    distance_km = Column(JSONB, nullable=False, default=dict)  # {region: km}
    process_fit = Column(JSONB, nullable=False, default=dict)  # {process: score}
    material_fit = Column(JSONB, nullable=False, default=dict)  # {material: score}
    capabilities = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class ExchangeRate(Base):
    """Exchange rates — seeded with static rates, can be updated by cron/API."""
    __tablename__ = "exchange_rates"
    __table_args__ = (
        Index("ix_exchange_rates_pair", "from_currency", "to_currency"),
        {"schema": "geo"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    from_currency = Column(Text, nullable=False)
    to_currency = Column(Text, nullable=False, default="USD")
    rate = Column(Numeric(18, 8), nullable=False, default=1.0)
    source = Column(Text, nullable=False, default="seed")
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    is_current = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class TariffRule(Base):
    """Per-product-category tariff rules between origin/destination."""
    __tablename__ = "tariff_rules"
    __table_args__ = {"schema": "geo"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    origin_region = Column(Text, nullable=False)
    destination_region = Column(Text, nullable=False)
    product_category = Column(Text, nullable=True)  # null = default rate
    hs_code_prefix = Column(Text, nullable=True)
    tariff_pct = Column(Numeric(6, 4), nullable=False, default=0.0)
    notes = Column(Text, nullable=True)
    valid_from = Column(DateTime(timezone=True), default=datetime.utcnow)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
