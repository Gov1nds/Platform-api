"""
Migration 007: fulfillment execution tables.

Run:
  python migrations/m007_fulfillment_tables.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_007")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "ops"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "sourcing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "pricing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "auth"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.purchase_orders (
                id uuid PRIMARY KEY,
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                rfq_id uuid NOT NULL REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                po_number text NOT NULL UNIQUE,
                status text NOT NULL DEFAULT 'po_issued',
                vendor_confirmation_status text NOT NULL DEFAULT 'pending',
                vendor_confirmation_number text,
                issued_at timestamptz NOT NULL DEFAULT now(),
                confirmed_at timestamptz,
                confirmed_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                currency text NOT NULL DEFAULT 'USD',
                subtotal numeric(18,6),
                freight numeric(18,6),
                taxes numeric(18,6),
                total_amount numeric(18,6),
                notes text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_purchase_orders_rfq
            ON ops.purchase_orders (rfq_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_purchase_orders_project
            ON ops.purchase_orders (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_purchase_orders_vendor
            ON ops.purchase_orders (vendor_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_purchase_orders_status
            ON ops.purchase_orders (status)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.shipments (
                id uuid PRIMARY KEY,
                purchase_order_id uuid NOT NULL REFERENCES ops.purchase_orders(id) ON DELETE CASCADE,
                shipment_number text NOT NULL UNIQUE,
                carrier_name text,
                carrier_code text,
                tracking_number text,
                status text NOT NULL DEFAULT 'shipped',
                shipped_at timestamptz,
                eta timestamptz,
                delivered_at timestamptz,
                delay_reason text,
                origin text,
                destination text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_shipments_po
            ON ops.shipments (purchase_order_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_shipments_status
            ON ops.shipments (status)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.shipment_events (
                id uuid PRIMARY KEY,
                shipment_id uuid NOT NULL REFERENCES ops.shipments(id) ON DELETE CASCADE,
                event_type text NOT NULL,
                event_status text NOT NULL DEFAULT 'recorded',
                location text,
                message text,
                occurred_at timestamptz NOT NULL DEFAULT now(),
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_shipment_events_shipment
            ON ops.shipment_events (shipment_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.carrier_milestones (
                id uuid PRIMARY KEY,
                shipment_id uuid NOT NULL REFERENCES ops.shipments(id) ON DELETE CASCADE,
                milestone_code text NOT NULL,
                milestone_name text NOT NULL,
                milestone_status text NOT NULL DEFAULT 'pending',
                description text,
                location text,
                estimated_at timestamptz,
                actual_at timestamptz,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_carrier_milestones_shipment
            ON ops.carrier_milestones (shipment_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.customs_events (
                id uuid PRIMARY KEY,
                shipment_id uuid NOT NULL REFERENCES ops.shipments(id) ON DELETE CASCADE,
                country text,
                status text NOT NULL DEFAULT 'pending',
                message text,
                held_reason text,
                released_at timestamptz,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_customs_events_shipment
            ON ops.customs_events (shipment_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.goods_receipts (
                id uuid PRIMARY KEY,
                purchase_order_id uuid NOT NULL REFERENCES ops.purchase_orders(id) ON DELETE CASCADE,
                shipment_id uuid REFERENCES ops.shipments(id) ON DELETE SET NULL,
                receipt_number text NOT NULL UNIQUE,
                receipt_status text NOT NULL DEFAULT 'pending',
                received_quantity numeric(18,6),
                confirmed_at timestamptz,
                confirmed_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                notes text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_goods_receipts_po
            ON ops.goods_receipts (purchase_order_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.invoices (
                id uuid PRIMARY KEY,
                purchase_order_id uuid NOT NULL REFERENCES ops.purchase_orders(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                invoice_number text NOT NULL UNIQUE,
                invoice_date timestamptz,
                due_date timestamptz,
                invoice_status text NOT NULL DEFAULT 'issued',
                currency text NOT NULL DEFAULT 'USD',
                subtotal numeric(18,6),
                taxes numeric(18,6),
                total_amount numeric(18,6),
                matched_at timestamptz,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_invoices_po
            ON ops.invoices (purchase_order_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_invoices_status
            ON ops.invoices (invoice_status)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ops.payment_states (
                id uuid PRIMARY KEY,
                invoice_id uuid NOT NULL UNIQUE REFERENCES ops.invoices(id) ON DELETE CASCADE,
                purchase_order_id uuid NOT NULL REFERENCES ops.purchase_orders(id) ON DELETE CASCADE,
                status text NOT NULL DEFAULT 'unpaid',
                paid_at timestamptz,
                payment_reference text,
                notes text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_payment_states_invoice
            ON ops.payment_states (invoice_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_payment_states_status
            ON ops.payment_states (status)
        """))

    logger.info("Migration 007 complete.")


if __name__ == "__main__":
    run()