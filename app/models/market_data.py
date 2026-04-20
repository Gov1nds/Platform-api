"""
Market-data entities — external feeds cached in Repo C.

Contract anchors
----------------
§2.31 Baseline_Price        §2.32 Forex_Rate
§2.33 Tariff_Rate           §2.34 Logistics_Rate
§2.35 Commodity_Price_Tick
§3.17 Freshness (SM-014)    §3.56 Baseline_Price.source_type
§3.58 Forex_Rate.source     §3.59 Carriers (CN-10 reconciled to ``other``)
§3.60 Commodity exchange    §12   Data-freshness rules and TTLs

Invariants
----------
* Rows with non-null ``locked_for_quote_id`` / ``locked_for_po_id`` are
  IMMUTABLE and retained permanently. Enforced at the service layer
  (application + audit trail).
* ``freshness_status`` transitions are service-managed: FRESH → STALE →
  EXPIRED by TTL; any non-null lock flips the row to LOCKED (terminal).
* Partial indexes ``WHERE freshness_status = 'FRESH'`` back lookup hot paths.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    country_code,
    currency_code,
    enum_check,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    BaselinePriceSourceType,
    Carrier,
    CommodityExchange,
    ForexRateSource,
    FreshnessStatus,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# BaselinePrice (§2.31)
# ─────────────────────────────────────────────────────────────────────────────


class BaselinePrice(Base, CreatedAtMixin):
    """Price band (floor / mid / ceiling) fetched from a market / distributor
    feed. At least one of ``part_id`` or ``commodity_group`` is non-null.

    TTL varies by ``source_type``:
    * distributor → 24 hours (contract §12.1)
    * market → 1–24 hours depending on commodity
    * historical → version-pinned
    """

    __tablename__ = "baseline_price"

    price_id: Mapped[uuid.UUID] = uuid_pk()
    part_id: Mapped[uuid.UUID | None] = uuid_fk(
        "part_master.part_id", ondelete="CASCADE", nullable=True
    )
    commodity_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quantity_break: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default=text("1")
    )
    price_floor: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price_mid: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price_ceiling: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = currency_code()
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    data_source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = tstz(default_now=True)
    valid_until: Mapped[datetime] = tstz()
    freshness_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'FRESH'")
    )

    __table_args__ = (
        enum_check("source_type", values_of(BaselinePriceSourceType)),
        enum_check("freshness_status", values_of(FreshnessStatus)),
        CheckConstraint("quantity_break > 0", name="quantity_break_positive"),
        CheckConstraint(
            "price_floor <= price_mid AND price_mid <= price_ceiling",
            name="price_band_ordered",
        ),
        CheckConstraint(
            "part_id IS NOT NULL OR commodity_group IS NOT NULL",
            name="part_or_commodity_required",
        ),
        UniqueConstraint(
            "part_id",
            "region",
            "quantity_break",
            "data_source_name",
            "fetched_at",
            name="uq_baseline_price_ingest_identity",
        ),
        Index(
            "ix_baseline_price_part_id_region_quantity_break",
            "part_id",
            "region",
            "quantity_break",
        ),
        Index(
            "ix_baseline_price_commodity_group_region",
            "commodity_group",
            "region",
        ),
        Index(
            "ix_baseline_price_freshness_status_fresh",
            "freshness_status",
            postgresql_where=text("freshness_status = 'FRESH'"),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ForexRate (§2.32)
# ─────────────────────────────────────────────────────────────────────────────


class ForexRate(Base, CreatedAtMixin):
    """Currency-pair rate. TTL: 15 minutes (contract §12.1).

    Invariant: when ``locked_for_quote_id`` or ``locked_for_po_id`` is
    non-null, the row is IMMUTABLE and retained permanently (CN-9).
    """

    __tablename__ = "forex_rate"

    rate_id: Mapped[uuid.UUID] = uuid_pk()
    from_currency: Mapped[str] = currency_code()
    to_currency: Mapped[str] = currency_code()
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fetched_at: Mapped[datetime] = tstz(default_now=True)
    valid_until: Mapped[datetime] = tstz()
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    locked_for_quote_id: Mapped[uuid.UUID | None] = uuid_fk(
        "quote.quote_id", ondelete="RESTRICT", nullable=True
    )
    locked_for_po_id: Mapped[uuid.UUID | None] = uuid_fk(
        "purchase_order.po_id", ondelete="RESTRICT", nullable=True
    )
    freshness_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'FRESH'")
    )

    __table_args__ = (
        enum_check("source", values_of(ForexRateSource)),
        enum_check("freshness_status", values_of(FreshnessStatus)),
        CheckConstraint("from_currency <> to_currency", name="from_to_currency_distinct"),
        CheckConstraint("rate > 0", name="rate_positive"),
        Index(
            "ix_forex_rate_from_to_fetched",
            "from_currency",
            "to_currency",
            "fetched_at",
        ),
        Index("ix_forex_rate_locked_for_quote_id", "locked_for_quote_id"),
        Index("ix_forex_rate_locked_for_po_id", "locked_for_po_id"),
        Index(
            "ix_forex_rate_freshness_status_fresh",
            "freshness_status",
            postgresql_where=text("freshness_status = 'FRESH'"),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TariffRate (§2.33)
# ─────────────────────────────────────────────────────────────────────────────


class TariffRate(Base, CreatedAtMixin):
    """HS-code based import duty / VAT rate for a country-pair lane.

    TTL: 7 days (contract §12.1).
    """

    __tablename__ = "tariff_rate"

    tariff_id: Mapped[uuid.UUID] = uuid_pk()
    hs_code: Mapped[str] = mapped_column(String(12), nullable=False)
    from_country: Mapped[str] = country_code()
    to_country: Mapped[str] = country_code()
    duty_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), nullable=False, server_default=text("0")
    )
    fta_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    fta_agreement_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = tstz(default_now=True)
    valid_until: Mapped[datetime] = tstz()
    locked_for_quote_id: Mapped[uuid.UUID | None] = uuid_fk(
        "quote.quote_id", ondelete="RESTRICT", nullable=True
    )
    freshness_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'FRESH'")
    )

    __table_args__ = (
        enum_check("freshness_status", values_of(FreshnessStatus)),
        CheckConstraint("duty_rate >= 0", name="duty_rate_nonneg"),
        CheckConstraint("vat_rate >= 0", name="vat_rate_nonneg"),
        Index(
            "ix_tariff_rate_hs_from_to_effective",
            "hs_code",
            "from_country",
            "to_country",
            "effective_date",
        ),
        Index("ix_tariff_rate_locked_for_quote_id", "locked_for_quote_id"),
        Index(
            "ix_tariff_rate_freshness_fresh",
            "freshness_status",
            postgresql_where=text("freshness_status = 'FRESH'"),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# LogisticsRate (§2.34)
# ─────────────────────────────────────────────────────────────────────────────


class LogisticsRate(Base, CreatedAtMixin):
    """Carrier lane-rate estimate. CN-10: carrier vocabulary normalised to
    ``DHL|FedEx|UPS|Maersk|other`` (both ``logistics_rate.carrier`` and
    ``shipment.carrier``). TTL: 24 hours."""

    __tablename__ = "logistics_rate"

    logistics_id: Mapped[uuid.UUID] = uuid_pk()
    origin_country: Mapped[str] = country_code()
    destination_country: Mapped[str] = country_code()
    carrier: Mapped[str] = mapped_column(String(16), nullable=False)
    service_level: Mapped[str] = mapped_column(String(64), nullable=False)
    weight_band: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = currency_code()
    transit_days_min: Mapped[int] = mapped_column(Integer, nullable=False)
    transit_days_max: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = tstz(default_now=True)
    valid_until: Mapped[datetime] = tstz()
    freshness_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'FRESH'")
    )

    __table_args__ = (
        enum_check("carrier", values_of(Carrier)),
        enum_check("freshness_status", values_of(FreshnessStatus)),
        CheckConstraint("transit_days_min >= 0", name="transit_days_min_nonneg"),
        CheckConstraint(
            "transit_days_max >= transit_days_min",
            name="transit_days_max_gte_min",
        ),
        Index(
            "ix_logistics_rate_lane",
            "origin_country",
            "destination_country",
            "carrier",
            "service_level",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CommodityPriceTick (§2.35)
# ─────────────────────────────────────────────────────────────────────────────


class CommodityPriceTick(Base, CreatedAtMixin):
    """Per-tick commodity feed from LME / Fastmarkets / CME."""

    __tablename__ = "commodity_price_tick"

    tick_id: Mapped[uuid.UUID] = uuid_pk()
    commodity: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = currency_code()
    fetched_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        enum_check("exchange", values_of(CommodityExchange)),
        Index(
            "ix_commodity_price_tick_commodity_exchange_fetched",
            "commodity",
            "exchange",
            "fetched_at",
        ),
    )


__all__ = [
    "BaselinePrice",
    "ForexRate",
    "TariffRate",
    "LogisticsRate",
    "CommodityPriceTick",
]
