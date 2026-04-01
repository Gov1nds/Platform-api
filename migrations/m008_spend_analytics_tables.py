"""
Migration 008: analytics / spend ledger tables.

Run:
  python migrations/m008_spend_analytics_tables.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_008")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "analytics"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "sourcing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "pricing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "ops"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "auth"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.spend_ledger (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                rfq_id uuid REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                purchase_order_id uuid REFERENCES ops.purchase_orders(id) ON DELETE SET NULL,
                shipment_id uuid REFERENCES ops.shipments(id) ON DELETE SET NULL,
                invoice_id uuid REFERENCES ops.invoices(id) ON DELETE SET NULL,
                ledger_type text NOT NULL,
                source_type text NOT NULL,
                source_id text NOT NULL,
                category text NOT NULL DEFAULT 'uncategorized',
                region text,
                currency text NOT NULL DEFAULT 'USD',
                quantity numeric(18,6),
                unit_price numeric(18,6),
                amount numeric(18,6) NOT NULL DEFAULT 0,
                baseline_amount numeric(18,6),
                realized_savings numeric(18,6),
                occurred_at timestamptz NOT NULL DEFAULT now(),
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_spend_ledger_dedupe
            ON analytics.spend_ledger (source_type, source_id, ledger_type, category)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_spend_ledger_project
            ON analytics.spend_ledger (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_spend_ledger_vendor
            ON analytics.spend_ledger (vendor_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_spend_ledger_category
            ON analytics.spend_ledger (category)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_spend_ledger_occurred_at
            ON analytics.spend_ledger (occurred_at)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.category_spend_rollups (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                period_month timestamptz NOT NULL,
                category text NOT NULL,
                currency text NOT NULL DEFAULT 'USD',
                committed_spend numeric(18,6) NOT NULL DEFAULT 0,
                invoiced_spend numeric(18,6) NOT NULL DEFAULT 0,
                paid_spend numeric(18,6) NOT NULL DEFAULT 0,
                savings_realized numeric(18,6) NOT NULL DEFAULT 0,
                line_count integer NOT NULL DEFAULT 0,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_category_spend_rollups_project
            ON analytics.category_spend_rollups (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_category_spend_rollups_period
            ON analytics.category_spend_rollups (period_month)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.vendor_spend_rollups (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                vendor_name text,
                period_month timestamptz NOT NULL,
                currency text NOT NULL DEFAULT 'USD',
                committed_spend numeric(18,6) NOT NULL DEFAULT 0,
                invoiced_spend numeric(18,6) NOT NULL DEFAULT 0,
                paid_spend numeric(18,6) NOT NULL DEFAULT 0,
                savings_realized numeric(18,6) NOT NULL DEFAULT 0,
                total_orders integer NOT NULL DEFAULT 0,
                on_time_shipments integer NOT NULL DEFAULT 0,
                late_shipments integer NOT NULL DEFAULT 0,
                avg_lead_time_days numeric(18,6),
                on_time_rate numeric(18,6),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_spend_rollups_project
            ON analytics.vendor_spend_rollups (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_spend_rollups_period
            ON analytics.vendor_spend_rollups (period_month)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.monthly_spend_snapshots (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                period_month timestamptz NOT NULL,
                currency text NOT NULL DEFAULT 'USD',
                committed_spend numeric(18,6) NOT NULL DEFAULT 0,
                invoiced_spend numeric(18,6) NOT NULL DEFAULT 0,
                paid_spend numeric(18,6) NOT NULL DEFAULT 0,
                savings_realized numeric(18,6) NOT NULL DEFAULT 0,
                quote_to_order_conversion numeric(18,6),
                vendor_on_time_rate numeric(18,6),
                avg_lead_time_days numeric(18,6),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_monthly_spend_snapshots_project
            ON analytics.monthly_spend_snapshots (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_monthly_spend_snapshots_period
            ON analytics.monthly_spend_snapshots (period_month)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.savings_realized (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                rfq_id uuid REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                purchase_order_id uuid REFERENCES ops.purchase_orders(id) ON DELETE SET NULL,
                invoice_id uuid REFERENCES ops.invoices(id) ON DELETE SET NULL,
                source_type text NOT NULL,
                source_id text NOT NULL,
                currency text NOT NULL DEFAULT 'USD',
                baseline_amount numeric(18,6),
                actual_amount numeric(18,6),
                realized_amount numeric(18,6),
                realized_at timestamptz,
                notes text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_savings_realized_source
            ON analytics.savings_realized (source_type, source_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.delivery_performance_rollups (
                id uuid PRIMARY KEY,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                vendor_name text,
                period_month timestamptz NOT NULL,
                currency text NOT NULL DEFAULT 'USD',
                total_shipments integer NOT NULL DEFAULT 0,
                on_time_shipments integer NOT NULL DEFAULT 0,
                late_shipments integer NOT NULL DEFAULT 0,
                on_time_rate numeric(18,6),
                avg_lead_time_days numeric(18,6),
                avg_delay_days numeric(18,6),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_delivery_perf_project
            ON analytics.delivery_performance_rollups (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_delivery_perf_period
            ON analytics.delivery_performance_rollups (period_month)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analytics.report_schedules (
                id uuid PRIMARY KEY,
                report_name text NOT NULL,
                report_type text NOT NULL,
                frequency text NOT NULL DEFAULT 'weekly',
                recipients_json jsonb NOT NULL DEFAULT '[]'::jsonb,
                filters_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                is_active boolean NOT NULL DEFAULT true,
                next_run_at timestamptz,
                last_run_at timestamptz,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_report_schedules_active
            ON analytics.report_schedules (is_active)
        """))

    logger.info("Migration 008 complete.")


if __name__ == "__main__":
    run()