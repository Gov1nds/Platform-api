"""015 phase3 market intelligence

Revision ID: 015_phase3_market_intelligence
Revises: 014_phase3_regional_strategy
Create Date: 2026-04-16 00:30:00.000000

Adds Phase 3 market intelligence tables:
  - pricing.commodity_price_signals (raw material index + buy-valley flag)
  - pricing.vendor_lead_time_history (actual vs quoted LT for distributions)
  - pricing.market_anomaly_events (outlier quote / LT detections)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "015_phase3_market_intelligence"
down_revision = "014_phase3_regional_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── commodity_price_signals ────────────────────────────────────────────
    op.create_table(
        "commodity_price_signals",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("commodity_name", sa.Text(), nullable=False),
        sa.Column("material_family_tag", sa.Text(), nullable=True),
        sa.Column("price_per_unit", sa.Numeric(20, 8), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False, server_default="kg"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="seed"),
        sa.Column("trend_direction", sa.String(length=10), nullable=True),
        sa.Column("trend_pct_30d", sa.Numeric(8, 4), nullable=True),
        sa.Column("is_valley", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index(
        "ix_commodity_price_signals_family_date",
        "commodity_price_signals",
        ["material_family_tag", "price_date"],
        schema="pricing",
    )
    op.create_index(
        "ix_commodity_price_signals_commodity_date",
        "commodity_price_signals",
        ["commodity_name", "price_date"],
        schema="pricing",
    )

    # ── vendor_lead_time_history (aggregate observations) ──────────────────
    op.create_table(
        "vendor_lead_time_history",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_tag", sa.Text(), nullable=True),
        sa.Column("material_family", sa.Text(), nullable=True),
        sa.Column("actual_lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("quoted_lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("deviation_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("source_rfq_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("source_po_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("recorded_at", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index(
        "ix_vendor_lt_history_p3_vendor_cat",
        "vendor_lead_time_history",
        ["vendor_id", "category_tag"],
        schema="pricing",
    )

    # ── market_anomaly_events ──────────────────────────────────────────────
    op.create_table(
        "market_anomaly_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("anomaly_type", sa.String(length=40), nullable=False),
        sa.Column("observed_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("expected_range_low", sa.Numeric(20, 8), nullable=True),
        sa.Column("expected_range_high", sa.Numeric(20, 8), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="MEDIUM"),
        sa.Column("auto_flagged", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_outcome", sa.String(length=40), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index(
        "ix_market_anomaly_events_vendor_type",
        "market_anomaly_events",
        ["vendor_id", "anomaly_type"],
        schema="pricing",
    )
    op.create_index(
        "ix_market_anomaly_events_severity",
        "market_anomaly_events",
        ["severity", "detected_at"],
        schema="pricing",
    )


def downgrade() -> None:
    op.drop_index("ix_market_anomaly_events_severity", table_name="market_anomaly_events", schema="pricing")
    op.drop_index("ix_market_anomaly_events_vendor_type", table_name="market_anomaly_events", schema="pricing")
    op.drop_table("market_anomaly_events", schema="pricing")

    op.drop_index("ix_vendor_lt_history_p3_vendor_cat", table_name="vendor_lead_time_history_phase3", schema="pricing")
    op.drop_table("vendor_lead_time_history_phase3", schema="pricing")

    op.drop_index("ix_commodity_price_signals_commodity_date", table_name="commodity_price_signals", schema="pricing")
    op.drop_index("ix_commodity_price_signals_family_date", table_name="commodity_price_signals", schema="pricing")
    op.drop_table("commodity_price_signals", schema="pricing")
