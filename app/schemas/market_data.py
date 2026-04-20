"""
market_data.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Market Data & Freshness Schema Layer

CONTRACT AUTHORITY: contract.md §2.31–2.35 (Baseline_Price, Forex_Rate,
Tariff_Rate, Logistics_Rate, Commodity_Price_Tick), §2.75 (Data_Freshness_Log),
§3.17 (SM-014: freshness states), §4.13 (Admin refresh endpoints).

Invariants encoded here:
  • LOCKED rows are IMMUTABLE; retained permanently for audit (SM-014).
  • EXPIRED rows cannot be used in new recommendations (LAW-5).
  • STALE rows only with explicit [STALE] warning surfaced in UI (LAW-5).
  • Forex_Rate rows with non-null locked_for_quote_id or locked_for_po_id
    are LOCKED and IMMUTABLE — never TTL-evicted (contract §2.32).
  • Tariff_Rate: locked_for_quote_id similarly immutable.
  • CN-10: Logistics_Rate.carrier and Shipment.carrier both use 'other'
    for unlisted carriers.
  • All monetary values: DECIMAL(20, 8).
  • All timestamps: TIMESTAMPTZ.
  • quantity_break > 0 CHECK on Baseline_Price.
  • transit_days_max >= transit_days_min CHECK on Logistics_Rate.
  • Freshness TTLs (from requirements.yaml):
      Forex:        15 min
      Commodity:    hourly
      Distributor:  daily
      Tariff:       weekly
      Logistics:    daily
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .common import (
    ForexRateSource,
    BaselinePriceSourceType,
    Carrier,
    CommodityExchange,
    CountryCode,
    CurrencyCode,
    FreshnessLogStatus,
    FreshnessStatus,
    HSCode,
    Money,
    PGIBase,
)


# ──────────────────────────────────────────────────────────────────────────
# Baseline_Price (contract §2.31)
# ──────────────────────────────────────────────────────────────────────────

class BaselinePriceSchema(PGIBase):
    """Market baseline price for a part or commodity group.

    Constraint: at least one of (part_id, commodity_group) must be non-null.
    quantity_break: the minimum order quantity this price band applies to.
    freshness_status: SM-014 — FRESH | STALE | EXPIRED | LOCKED.
    """

    price_id: UUID
    part_id: Optional[UUID] = None
    commodity_group: Optional[str] = Field(default=None, max_length=128)
    quantity_break: Decimal = Field(gt=Decimal("0"))
    price_floor: Money
    price_mid: Money
    price_ceiling: Money
    currency: CurrencyCode
    region: str = Field(max_length=64)
    source_type: BaselinePriceSourceType
    data_source_name: str = Field(max_length=128)
    fetched_at: datetime
    valid_until: datetime
    freshness_status: FreshnessStatus = FreshnessStatus.FRESH

    @model_validator(mode="after")
    def part_or_commodity_required(self) -> "BaselinePriceSchema":
        if self.part_id is None and not self.commodity_group:
            raise ValueError(
                "At least one of (part_id, commodity_group) must be non-null."
            )
        return self

    @model_validator(mode="after")
    def price_band_ordering(self) -> "BaselinePriceSchema":
        if not (self.price_floor <= self.price_mid <= self.price_ceiling):
            raise ValueError(
                "price_floor <= price_mid <= price_ceiling must hold."
            )
        return self


class BaselinePriceFreshnessSummary(PGIBase):
    """Freshness summary shown alongside any price in the UI (LAW-1)."""

    price_id: UUID
    fetched_at: datetime
    freshness_status: FreshnessStatus
    data_source_name: str
    warning: Optional[str] = Field(
        default=None,
        description="Non-null when STALE or EXPIRED — must be rendered in UI.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Forex_Rate (contract §2.32)
# ──────────────────────────────────────────────────────────────────────────

class ForexRateSchema(PGIBase):
    """Foreign exchange rate between two currencies.

    APPEND-ONLY strategy: new rate creates a new row; old rows are not updated.
    locked_for_quote_id / locked_for_po_id: when set, the row is LOCKED and
    IMMUTABLE — freshness TTL no longer applies.

    Contract invariant (§2.32): to_currency <> from_currency.
    """

    rate_id: UUID
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Decimal = Field(gt=Decimal("0"), decimal_places=8, max_digits=20)
    fetched_at: datetime
    valid_until: datetime
    source: ForexRateSource
    locked_for_quote_id: Optional[UUID] = None
    locked_for_po_id: Optional[UUID] = None
    freshness_status: FreshnessStatus = FreshnessStatus.FRESH

    @field_validator("to_currency")
    @classmethod
    def currencies_must_differ(cls, v: str, info: Any) -> str:
        # Validate after from_currency is available
        from_currency = (info.data or {}).get("from_currency")
        if from_currency and v == from_currency:
            raise ValueError("to_currency must differ from from_currency.")
        return v


class ForexLockSchema(PGIBase):
    """Record of a forex rate locked to a specific quote or PO.

    Used by Repo C when locking forex at quote submission (SM-014).
    """

    rate_id: UUID
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Decimal
    fetched_at: datetime
    locked_for_quote_id: Optional[UUID] = None
    locked_for_po_id: Optional[UUID] = None
    freshness_status: FreshnessStatus  # Always LOCKED after this operation


# ──────────────────────────────────────────────────────────────────────────
# Tariff_Rate (contract §2.33)
# ──────────────────────────────────────────────────────────────────────────

class TariffRateSchema(PGIBase):
    """Import duty and VAT rate for a specific HS code and country pair.

    APPEND-ONLY with effective_date versioning.
    locked_for_quote_id: when set, row is LOCKED for audit (CN-9).
    """

    tariff_id: UUID
    hs_code: HSCode
    from_country: CountryCode
    to_country: CountryCode
    duty_rate: Decimal = Field(ge=Decimal("0"), decimal_places=4)
    vat_rate: Decimal = Field(ge=Decimal("0"), decimal_places=4, default=Decimal("0"))
    fta_eligible: bool = False
    fta_agreement_name: Optional[str] = Field(default=None, max_length=128)
    effective_date: date = Field(description="ISO date YYYY-MM-DD.")
    fetched_at: datetime
    locked_for_quote_id: Optional[UUID] = None
    freshness_status: FreshnessStatus = FreshnessStatus.FRESH


# ──────────────────────────────────────────────────────────────────────────
# Logistics_Rate (contract §2.34)
# ──────────────────────────────────────────────────────────────────────────

class LogisticsRateSchema(PGIBase):
    """Estimated freight rate for a carrier route and weight band.

    CN-10: carrier uses 'other' (not 'custom') for unlisted carriers.
    transit_days_max >= transit_days_min enforced by CHECK constraint.
    """

    logistics_id: UUID
    origin_country: CountryCode
    destination_country: CountryCode
    carrier: Carrier
    service_level: str = Field(max_length=64)
    weight_band: str = Field(max_length=32)
    cost_estimate: Money
    currency: CurrencyCode
    transit_days_min: int = Field(ge=0)
    transit_days_max: int = Field(ge=0)
    fetched_at: datetime
    valid_until: datetime
    freshness_status: FreshnessStatus = FreshnessStatus.FRESH

    @model_validator(mode="after")
    def transit_days_ordering(self) -> "LogisticsRateSchema":
        if self.transit_days_max < self.transit_days_min:
            raise ValueError("transit_days_max must be >= transit_days_min.")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Commodity_Price_Tick (contract §2.35)
# ──────────────────────────────────────────────────────────────────────────

class CommodityPriceTickSchema(PGIBase):
    """A single commodity spot price observation from an exchange.

    Append-only — one row per (commodity, exchange, fetched_at).
    Used to compute price_band for raw-material components.
    """

    tick_id: UUID
    commodity: str = Field(max_length=64)
    exchange: CommodityExchange
    price: Money
    currency: CurrencyCode
    fetched_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Snapshot_Metadata (contract §2.88)
# ──────────────────────────────────────────────────────────────────────────

class SnapshotMetadataSchema(PGIBase):
    """Build metadata for a nightly snapshot table rebuild."""

    metadata_id: UUID
    snapshot_table: str = Field(max_length=64)
    snapshot_date: date = Field(description="ISO date YYYY-MM-DD.")
    source_rows: int = Field(ge=0)
    built_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Data_Freshness_Log (contract §2.75)
# ──────────────────────────────────────────────────────────────────────────

class DataFreshnessLogSchema(PGIBase):
    """Audit record of each market data refresh attempt.

    Written by Repo C every time it attempts to fetch external market data,
    regardless of success or failure (LAW-1, LAW-5).
    """

    log_id: UUID
    table_name: str = Field(max_length=64)
    record_id: UUID
    source_api: str = Field(max_length=128)
    status: FreshnessLogStatus
    previous_value_json: Optional[dict[str, Any]] = None
    new_value_json: Optional[dict[str, Any]] = None
    fetched_at: datetime
    error_message: Optional[str] = None


class DataFreshnessLogListResponse(PGIBase):
    """Paginated freshness log for GET /api/v1/admin/freshness-log."""

    items: list[DataFreshnessLogSchema]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Market context package (assembled by Repo C, forwarded to Repo B)
# ──────────────────────────────────────────────────────────────────────────

class MarketContextPackage(PGIBase):
    """Pre-assembled, freshness-validated market context forwarded to Repo B.

    Repo B NEVER fetches external data — all market context is pre-supplied
    in each request payload by Repo C (architectural invariant).
    All records have been freshness-validated by Repo C before packaging.
    """

    baseline_prices: list[BaselinePriceSchema] = Field(default_factory=list)
    tariff_snapshot: list[TariffRateSchema] = Field(default_factory=list)
    logistics_snapshot: list[LogisticsRateSchema] = Field(default_factory=list)
    forex_snapshot: list[ForexRateSchema] = Field(default_factory=list)
    assembled_at: datetime
    any_stale: bool = Field(
        default=False,
        description="True when at least one record is STALE — downstream must surface warning.",
    )
    stale_tables: list[str] = Field(
        default_factory=list,
        description="List of table names with STALE records in this package.",
    )