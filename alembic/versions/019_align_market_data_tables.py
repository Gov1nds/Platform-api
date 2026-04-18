"""Align market data tables with Blueprint §21.4.
Revision ID: 019
Revises: 018
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "019"
down_revision = "018"

def upgrade():
    # Baseline_Price
    op.execute("""
        CREATE TABLE IF NOT EXISTS baseline_price (
            price_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            part_id UUID REFERENCES part_master(part_id) ON DELETE CASCADE,
            commodity_group TEXT,
            quantity_break INTEGER NOT NULL DEFAULT 1,
            price_floor NUMERIC(20, 8) NOT NULL,
            price_mid NUMERIC(20, 8) NOT NULL,
            price_ceiling NUMERIC(20, 8) NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'USD',
            region TEXT,
            source_type TEXT NOT NULL DEFAULT 'distributor',
            data_source_name TEXT,
            sources_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            valid_until TIMESTAMPTZ,
            freshness_status TEXT NOT NULL DEFAULT 'FRESH',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(part_id, quantity_break)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_baseline_price_part_qty ON baseline_price (part_id, quantity_break)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_baseline_price_fresh ON baseline_price (freshness_status, fetched_at DESC) WHERE freshness_status = 'FRESH'")
    # FXRate extensions
    op.execute("ALTER TABLE fx_rates ADD COLUMN IF NOT EXISTS locked_for_quote_id UUID")
    op.execute("ALTER TABLE fx_rates ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ")
    op.execute("ALTER TABLE fx_rates ADD COLUMN IF NOT EXISTS freshness_status TEXT NOT NULL DEFAULT 'FRESH'")
    # Tariff extensions
    op.execute("ALTER TABLE tariff_schedules ADD COLUMN IF NOT EXISTS fta_eligible BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE tariff_schedules ADD COLUMN IF NOT EXISTS fta_agreement_name TEXT")
    op.execute("ALTER TABLE tariff_schedules ADD COLUMN IF NOT EXISTS locked_for_quote_id UUID")
    op.execute("ALTER TABLE tariff_schedules ADD COLUMN IF NOT EXISTS freshness_status TEXT NOT NULL DEFAULT 'FRESH'")
    # Logistics_Rate
    op.execute("""
        CREATE TABLE IF NOT EXISTS logistics_rate (
            logistics_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            origin_country CHAR(2) NOT NULL,
            destination_country CHAR(2) NOT NULL,
            origin_city TEXT,
            destination_city TEXT,
            carrier TEXT NOT NULL,
            service_level TEXT NOT NULL,
            weight_band TEXT NOT NULL,
            cost_estimate NUMERIC(20, 4) NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'USD',
            transit_days_min INTEGER NOT NULL,
            transit_days_max INTEGER NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            valid_until TIMESTAMPTZ,
            freshness_status TEXT NOT NULL DEFAULT 'FRESH',
            UNIQUE(origin_country, destination_country, carrier, service_level, weight_band)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_logistics_lane ON logistics_rate (origin_country, destination_country, carrier, service_level)")
    # Scoring cache columns (Task 22)
    op.execute("ALTER TABLE bom_lines ADD COLUMN IF NOT EXISTS score_cache_key TEXT")
    op.execute("ALTER TABLE bom_lines ADD COLUMN IF NOT EXISTS score_cache_valid_until TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bom_lines_score_cache_valid ON bom_lines (score_cache_key) WHERE score_cache_valid_until > NOW()")

def downgrade():
    pass
