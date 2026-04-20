"""0008 — Market data tables: baseline_price, forex_rate, tariff_rate,
logistics_rate, commodity_price_tick.

Revision ID: 0008
Revises: 0007
Create Date: 2024-01-01

Contract anchors:
  §2.31 Baseline_Price     §2.32 Forex_Rate
  §2.33 Tariff_Rate        §2.34 Logistics_Rate
  §2.35 Commodity_Price_Tick
  §3.17 freshness_status SM-014
  §3.56-§3.60 various status enums

Notes:
  - forex_rate.locked_for_quote_id and locked_for_po_id added in 0017
    (deferred FKs — quote/purchase_order tables not yet created).
  - tariff_rate.locked_for_quote_id added in 0017.
  - Rows with locked_for_* are IMMUTABLE (enforced by application).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MD = "market_data"
INTEL = "intelligence"

FRESHNESS_STATUS_CHECK = "freshness_status IN ('FRESH','STALE','EXPIRED','LOCKED')"


def upgrade() -> None:

    # ── baseline_price (§2.31) ───────────────────────────────────────────────
    op.create_table(
        "baseline_price",
        sa.Column(
            "price_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.part_master.part_id",
                ondelete="CASCADE",
                name="fk_baseline_price_part_id_part_master",
            ),
            nullable=True,
        ),
        sa.Column("commodity_group", sa.String(128), nullable=True),
        sa.Column(
            "quantity_break",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("price_floor", sa.Numeric(20, 8), nullable=False),
        sa.Column("price_mid", sa.Numeric(20, 8), nullable=False),
        sa.Column("price_ceiling", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("data_source_name", sa.String(128), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "freshness_status",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'FRESH'"),
        ),
        sa.CheckConstraint(
            "source_type IN ('distributor','market','historical')",
            name="ck_baseline_price_source_type",
        ),
        sa.CheckConstraint(FRESHNESS_STATUS_CHECK, name="ck_baseline_price_freshness_status"),
        sa.CheckConstraint("quantity_break > 0", name="ck_baseline_price_quantity_break_pos"),
        sa.CheckConstraint(
            "part_id IS NOT NULL OR commodity_group IS NOT NULL",
            name="ck_baseline_price_at_least_one_scope",
        ),
        schema=MD,
    )
    op.create_index(
        "ix_baseline_price_part_id_region_quantity_break",
        "baseline_price",
        ["part_id", "region", "quantity_break"],
        schema=MD,
    )
    op.create_index(
        "ix_baseline_price_commodity_group_region",
        "baseline_price",
        ["commodity_group", "region"],
        schema=MD,
    )
    op.create_index(
        "ix_baseline_price_freshness_status_fresh",
        "baseline_price",
        ["freshness_status"],
        schema=MD,
        postgresql_where=sa.text("freshness_status = 'FRESH'"),
    )

    # ── forex_rate (§2.32) ───────────────────────────────────────────────────
    # locked_for_quote_id, locked_for_po_id columns added in 0017
    op.create_table(
        "forex_rate",
        sa.Column(
            "rate_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("from_currency", sa.String(3), nullable=False),
        sa.Column("to_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "freshness_status",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'FRESH'"),
        ),
        sa.CheckConstraint(
            "to_currency <> from_currency", name="ck_forex_rate_no_self_pair"
        ),
        sa.CheckConstraint("rate > 0", name="ck_forex_rate_rate_positive"),
        sa.CheckConstraint(
            "source IN ('open_exchange_rates','xe_api')", name="ck_forex_rate_source"
        ),
        sa.CheckConstraint(FRESHNESS_STATUS_CHECK, name="ck_forex_rate_freshness_status"),
        schema=MD,
    )
    op.create_index(
        "ix_forex_rate_from_currency_to_currency_fetched_at",
        "forex_rate",
        ["from_currency", "to_currency", "fetched_at"],
        schema=MD,
    )
    op.create_index(
        "ix_forex_rate_freshness_status_fresh",
        "forex_rate",
        ["freshness_status"],
        schema=MD,
        postgresql_where=sa.text("freshness_status = 'FRESH'"),
    )

    # ── tariff_rate (§2.33) ──────────────────────────────────────────────────
    # locked_for_quote_id added in 0017
    op.create_table(
        "tariff_rate",
        sa.Column(
            "tariff_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("hs_code", sa.String(12), nullable=False),
        sa.Column("from_country", sa.String(2), nullable=False),
        sa.Column("to_country", sa.String(2), nullable=False),
        sa.Column("duty_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column(
            "vat_rate",
            sa.Numeric(7, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "fta_eligible",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("fta_agreement_name", sa.String(128), nullable=True),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "freshness_status",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'FRESH'"),
        ),
        sa.CheckConstraint("duty_rate >= 0", name="ck_tariff_rate_duty_rate_nonneg"),
        sa.CheckConstraint("vat_rate >= 0", name="ck_tariff_rate_vat_rate_nonneg"),
        sa.CheckConstraint(FRESHNESS_STATUS_CHECK, name="ck_tariff_rate_freshness_status"),
        schema=MD,
    )
    op.create_index(
        "ix_tariff_rate_hs_code_from_country_to_country_effective_date",
        "tariff_rate",
        ["hs_code", "from_country", "to_country", "effective_date"],
        schema=MD,
    )

    # ── logistics_rate (§2.34) ───────────────────────────────────────────────
    op.create_table(
        "logistics_rate",
        sa.Column(
            "logistics_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("origin_country", sa.String(2), nullable=False),
        sa.Column("destination_country", sa.String(2), nullable=False),
        sa.Column("carrier", sa.String(16), nullable=False),
        sa.Column("service_level", sa.String(64), nullable=False),
        sa.Column("weight_band", sa.String(32), nullable=False),
        sa.Column("cost_estimate", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("transit_days_min", sa.Integer, nullable=False),
        sa.Column("transit_days_max", sa.Integer, nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "freshness_status",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'FRESH'"),
        ),
        # CN-10: both carrier fields use 'other' (not 'custom')
        sa.CheckConstraint(
            "carrier IN ('DHL','FedEx','UPS','Maersk','other')",
            name="ck_logistics_rate_carrier",
        ),
        sa.CheckConstraint("transit_days_min >= 0", name="ck_logistics_rate_transit_days_min"),
        sa.CheckConstraint(
            "transit_days_max >= transit_days_min",
            name="ck_logistics_rate_transit_days_max_gte_min",
        ),
        sa.CheckConstraint(FRESHNESS_STATUS_CHECK, name="ck_logistics_rate_freshness_status"),
        schema=MD,
    )
    op.create_index(
        "ix_logistics_rate_origin_destination_carrier_service_level",
        "logistics_rate",
        ["origin_country", "destination_country", "carrier", "service_level"],
        schema=MD,
    )

    # ── commodity_price_tick (§2.35) ──────────────────────────────────────────
    op.create_table(
        "commodity_price_tick",
        sa.Column(
            "tick_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("commodity", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "exchange IN ('LME','Fastmarkets','CME')",
            name="ck_commodity_price_tick_exchange",
        ),
        schema=MD,
    )
    op.create_index(
        "ix_commodity_price_tick_commodity_exchange_fetched_at",
        "commodity_price_tick",
        ["commodity", "exchange", "fetched_at"],
        schema=MD,
    )


def downgrade() -> None:
    op.drop_table("commodity_price_tick", schema=MD)
    op.drop_table("logistics_rate", schema=MD)
    op.drop_table("tariff_rate", schema=MD)
    op.drop_table("forex_rate", schema=MD)
    op.drop_table("baseline_price", schema=MD)