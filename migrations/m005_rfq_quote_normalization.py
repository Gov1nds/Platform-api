"""
Migration 005: normalized RFQ quote lifecycle tables.

Run:
  python migrations/m005_rfq_quote_normalization.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_005")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "sourcing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "pricing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "bom"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "auth"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sourcing.rfq_quote_headers (
                id uuid PRIMARY KEY,
                rfq_batch_id uuid NOT NULL REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                quote_number text,
                quote_status text NOT NULL DEFAULT 'received',
                response_status text NOT NULL DEFAULT 'received',
                quote_currency varchar(3) NOT NULL DEFAULT 'USD',
                subtotal numeric(18,6),
                freight numeric(18,6),
                taxes numeric(18,6),
                total numeric(18,6),
                vendor_response_deadline timestamptz,
                sent_at timestamptz,
                received_at timestamptz,
                expires_at timestamptz,
                valid_until timestamptz,
                source_snapshot_id uuid,
                response_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_quote_headers_rfq
            ON sourcing.rfq_quote_headers (rfq_batch_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_quote_headers_vendor
            ON sourcing.rfq_quote_headers (vendor_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_quote_headers_status
            ON sourcing.rfq_quote_headers (quote_status)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sourcing.rfq_quote_lines (
                id uuid PRIMARY KEY,
                quote_header_id uuid NOT NULL REFERENCES sourcing.rfq_quote_headers(id) ON DELETE CASCADE,
                rfq_batch_id uuid NOT NULL REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                rfq_item_id uuid NOT NULL REFERENCES sourcing.rfq_items(id) ON DELETE CASCADE,
                bom_part_id uuid NOT NULL REFERENCES bom.bom_parts(id) ON DELETE CASCADE,
                part_name text,
                quantity numeric(18,6) NOT NULL DEFAULT 1,
                unit_price numeric(18,6),
                lead_time numeric(18,6),
                availability_status text NOT NULL DEFAULT 'unknown',
                compliance_status text NOT NULL DEFAULT 'unknown',
                moq numeric(18,6),
                risk_score numeric(12,6),
                line_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_quote_lines_header
            ON sourcing.rfq_quote_lines (quote_header_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_quote_lines_rfq_item
            ON sourcing.rfq_quote_lines (rfq_item_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sourcing.rfq_comparison_views (
                id uuid PRIMARY KEY,
                rfq_batch_id uuid NOT NULL REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                version integer NOT NULL DEFAULT 1,
                sort_by text NOT NULL DEFAULT 'total_cost',
                filters_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                comparison_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_comparison_views_rfq
            ON sourcing.rfq_comparison_views (rfq_batch_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_comparison_views_version
            ON sourcing.rfq_comparison_views (version)
        """))

    logger.info("Migration 005 complete.")


if __name__ == "__main__":
    run()