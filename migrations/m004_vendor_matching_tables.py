"""
Migration 004: vendor matching tables for persisted shortlist runs.

Run:
  python migrations/m004_vendor_matching_tables.py

Safe to re-run.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text, inspect
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_004")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "pricing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "auth"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pricing.vendor_match_runs (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                filters_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                constraints_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                strategy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
                analysis_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
                weights_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                total_vendors_considered integer NOT NULL DEFAULT 0,
                total_matches integer NOT NULL DEFAULT 0,
                shortlist_size integer NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_match_runs_project
            ON pricing.vendor_match_runs (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_match_runs_created
            ON pricing.vendor_match_runs (created_at DESC)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pricing.vendor_matches (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                match_run_id uuid NOT NULL REFERENCES pricing.vendor_match_runs(id) ON DELETE CASCADE,
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                vendor_id uuid NOT NULL REFERENCES pricing.vendors(id) ON DELETE CASCADE,
                rank integer NOT NULL DEFAULT 0,
                score numeric(12,6) NOT NULL DEFAULT 0,
                score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
                reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
                explanation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                constraint_inputs jsonb NOT NULL DEFAULT '{}'::jsonb,
                scorecard_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                part_rationales jsonb NOT NULL DEFAULT '[]'::jsonb,
                shortlist_status text NOT NULL DEFAULT 'shortlisted',
                response_status text NOT NULL DEFAULT 'uncontacted',
                feedback_rating numeric(6,2),
                feedback_notes text,
                is_primary boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_matches_run
            ON pricing.vendor_matches (match_run_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_matches_project
            ON pricing.vendor_matches (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_matches_vendor
            ON pricing.vendor_matches (vendor_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_matches_rank
            ON pricing.vendor_matches (rank)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_vendor_matches_score
            ON pricing.vendor_matches (score DESC)
        """))

    logger.info("Migration 004 complete.")


if __name__ == "__main__":
    run()