"""
Migration 002: Add production tracking and execution feedback tables
+ extended schema (catalog, pricing, sourcing improvements)

Run:
  python migrations/m002_add_tracking_tables.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text, inspect
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_002")


def table_exists(inspector, schema, table_name):
    return table_name in inspector.get_table_names(schema=schema)


def run():
    inspector = inspect(engine)

    with engine.begin() as conn:

        # ✅ REQUIRED FOR gen_random_uuid()
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

        # ===============================
        # 1. CORE TRACKING TABLES
        # ===============================

        if not table_exists(inspector, "ops", "production_tracking"):
            logger.info("Creating ops.production_tracking")
            conn.execute(text("""
                CREATE TABLE ops.production_tracking (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    rfq_id uuid NOT NULL REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                    stage text DEFAULT 'T0',
                    status_message text,
                    progress_percent integer DEFAULT 0,
                    updated_by text,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_ops_prod_tracking_rfq 
                ON ops.production_tracking (rfq_id)
            """))

        if not table_exists(inspector, "ops", "execution_feedback"):
            logger.info("Creating ops.execution_feedback")
            conn.execute(text("""
                CREATE TABLE ops.execution_feedback (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    rfq_id uuid NOT NULL UNIQUE REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                    predicted_cost numeric(18,6),
                    actual_cost numeric(18,6),
                    cost_delta numeric(18,6),
                    predicted_lead_time numeric(12,2),
                    actual_lead_time numeric(12,2),
                    lead_time_delta numeric(12,2),
                    feedback_notes text,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """))

        # ===============================
        # 2. TRIGGERS
        # ===============================

        for tbl in ['ops.production_tracking', 'ops.execution_feedback']:
            try:
                conn.execute(text(f"""
                    DROP TRIGGER IF EXISTS trg_set_updated_at ON {tbl}
                """))
                conn.execute(text(f"""
                    CREATE TRIGGER trg_set_updated_at
                    BEFORE UPDATE ON {tbl}
                    FOR EACH ROW
                    EXECUTE FUNCTION ops.set_updated_at()
                """))
            except Exception:
                pass

        # ===============================
        # 3. EXTENDED SCHEMA
        # ===============================

        logger.info("Applying extended schema updates...")

        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS catalog.part_identity_map (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          bom_part_id uuid REFERENCES bom.bom_parts(id) ON DELETE CASCADE,
          part_master_id uuid REFERENCES catalog.part_master(id) ON DELETE SET NULL,
          canonical_part_key text NOT NULL,
          match_method text NOT NULL CHECK (
            match_method IN ('exact_mpn','fuzzy_name','ml_match','manual','rule_based')
          ),
          confidence numeric(6,3) NOT NULL,
          is_primary boolean NOT NULL DEFAULT true,
          created_at timestamptz DEFAULT now()
        );
        """))

        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pricing.price_aggregates (
          canonical_part_key text,
          region_id uuid,
          best_unit_price numeric,
          avg_price numeric,
          min_lead_time numeric,
          vendor_count int,
          last_updated timestamptz,
          PRIMARY KEY (canonical_part_key, region_id)
        );
        """))

        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sourcing.manual_quote_requests (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          bom_part_id uuid NOT NULL,
          rfq_item_id uuid,
          status text CHECK (status IN ('pending','sent','received','closed')),
          assigned_to uuid,
          notes text,
          created_at timestamptz DEFAULT now()
        );
        """))

        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS catalog.part_versions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          part_master_id uuid,
          version_no int,
          snapshot jsonb,
          created_at timestamptz DEFAULT now(),
          UNIQUE(part_master_id, version_no)
        );
        """))

        # ===============================
        # 4. ALTER TABLES
        # ===============================

        conn.execute(text("""
        ALTER TABLE bom.bom_parts
        ADD COLUMN IF NOT EXISTS canonical_part_key text;
        """))

        conn.execute(text("""
        ALTER TABLE bom.bom_parts
        ADD COLUMN IF NOT EXISTS part_master_id uuid REFERENCES catalog.part_master(id);
        """))

        conn.execute(text("""
        ALTER TABLE sourcing.rfq_items
        ADD COLUMN IF NOT EXISTS canonical_part_key text;
        """))

        # ===============================
        # 5. INDEXES
        # ===============================

        conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_part_identity_map_key 
        ON catalog.part_identity_map (canonical_part_key);
        """))

        conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_bom_parts_canonical 
        ON bom.bom_parts (canonical_part_key);
        """))

        conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_rfq_items_canonical 
        ON sourcing.rfq_items (canonical_part_key);
        """))

    logger.info("Migration 002 complete.")


if __name__ == "__main__":
    run()