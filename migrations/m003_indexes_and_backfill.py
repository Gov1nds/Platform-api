"""
Migration 003: Add missing indexes, create catalog.part_master (referenced by m002 FK),
fix guest_session index, pricing query indexes, and backfill null-owned records.

Run:
  python migrations/m003_indexes_and_backfill.py

Safe to re-run: all operations use IF NOT EXISTS / IF EXISTS guards.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text, inspect
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_003")


def run():
    inspector = inspect(engine)

    with engine.begin() as conn:
        # ===============================
        # 0. Ensure schemas exist
        # ===============================
        for schema in ("catalog", "auth", "bom", "projects", "pricing", "sourcing", "ops", "geo"):
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

        # ===============================
        # 1. catalog.part_master — referenced by m002 FKs but never created
        # ===============================
        logger.info("Ensuring catalog.part_master exists...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS catalog.part_master (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_part_key text NOT NULL,
                domain text NOT NULL DEFAULT 'unknown',
                category text,
                procurement_class text,
                description text,
                mpn text,
                manufacturer text,
                material text,
                material_grade text,
                material_form text,
                specs jsonb NOT NULL DEFAULT '{}'::jsonb,
                aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
                review_status text NOT NULL DEFAULT 'auto',
                confidence numeric(6,3) NOT NULL DEFAULT 0,
                source text NOT NULL DEFAULT 'observed',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_part_master_canonical_key
            ON catalog.part_master (canonical_part_key)
        """))

        # ===============================
        # 2. catalog.alias_table — for MPN/name aliasing
        # ===============================
        logger.info("Ensuring catalog.alias_table exists...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS catalog.alias_table (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                part_master_id uuid REFERENCES catalog.part_master(id) ON DELETE CASCADE,
                alias_type text NOT NULL CHECK (alias_type IN ('mpn','name','supplier_pn','description')),
                alias_value text NOT NULL,
                normalized_value text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(alias_type, normalized_value)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_alias_normalized
            ON catalog.alias_table (alias_type, normalized_value)
        """))

        # ===============================
        # 3. Missing indexes for performance
        # ===============================
        logger.info("Adding missing indexes...")

        # Guest session lookup (used in merge)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_guest_sessions_token
            ON auth.guest_sessions (session_token)
        """))

        # BOM by guest_session_id (used in merge queries)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_boms_guest_session
            ON bom.boms (guest_session_id)
        """))

        # Pricing lookups: canonical_part_key + freshness_state
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_pricing_quotes_key_fresh
            ON pricing.pricing_quotes (canonical_part_key, freshness_state)
        """))

        # Pricing: recorded_at for ORDER BY
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_pricing_quotes_recorded
            ON pricing.pricing_quotes (recorded_at DESC)
        """))

        # BOM parts: category_code for group-by queries
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_bom_parts_category
            ON bom.bom_parts (category_code)
        """))

        # BOM parts: mpn for lookup
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_bom_parts_mpn
            ON bom.bom_parts (mpn) WHERE mpn IS NOT NULL AND mpn != ''
        """))

        # BOM parts: manufacturer
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_bom_parts_manufacturer
            ON bom.bom_parts (manufacturer) WHERE manufacturer IS NOT NULL AND manufacturer != ''
        """))

        # BOM parts: procurement_class
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_bom_parts_procurement
            ON bom.bom_parts (procurement_class)
        """))

        # Projects: guest_session_id for merge
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_projects_guest_session
            ON projects.projects (guest_session_id)
        """))

        # RFQ batches: guest_session_id for merge
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rfq_batches_guest_session
            ON sourcing.rfq_batches (guest_session_id)
        """))

        # User email unique (ORM lacks this constraint)
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_unique
            ON auth.users (email)
        """))

        # ===============================
        # 3b. ALTER TABLE — add missing columns
        # ===============================
        logger.info("Adding missing columns...")

        conn.execute(text("""
            ALTER TABLE bom.bom_parts
            ADD COLUMN IF NOT EXISTS review_status text DEFAULT 'auto'
        """))

        conn.execute(text("""
            ALTER TABLE bom.bom_parts
            ADD COLUMN IF NOT EXISTS canonical_part_key text
        """))

        conn.execute(text("""
            ALTER TABLE bom.bom_parts
            ADD COLUMN IF NOT EXISTS part_master_id uuid REFERENCES catalog.part_master(id) ON DELETE SET NULL
        """))

        conn.execute(text("""
            ALTER TABLE sourcing.rfq_items
            ADD COLUMN IF NOT EXISTS canonical_part_key text
        """))

        # ===============================
        # 4. Backfill: null-owned projects → link to BOM owner
        # ===============================
        logger.info("Backfilling null-owned projects...")
        result = conn.execute(text("""
            UPDATE projects.projects p
            SET user_id = b.uploaded_by_user_id, updated_at = now()
            FROM bom.boms b
            WHERE p.bom_id = b.id
              AND p.user_id IS NULL
              AND b.uploaded_by_user_id IS NOT NULL
        """))
        logger.info(f"  Fixed {result.rowcount} orphaned projects")

        # Backfill: null-owned analysis → link to BOM owner
        result = conn.execute(text("""
            UPDATE bom.analysis_results a
            SET user_id = b.uploaded_by_user_id, updated_at = now()
            FROM bom.boms b
            WHERE a.bom_id = b.id
              AND a.user_id IS NULL
              AND b.uploaded_by_user_id IS NOT NULL
        """))
        logger.info(f"  Fixed {result.rowcount} orphaned analysis results")

        # ===============================
        # 5. Add updated_at trigger for ops tables if not exists
        # ===============================
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION ops.set_updated_at()
            RETURNS trigger AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))

    logger.info("Migration 003 complete.")


if __name__ == "__main__":
    run()