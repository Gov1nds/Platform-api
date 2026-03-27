"""
Migration 002: Add production tracking and execution feedback tables.

These tables are used by the tracking_service but are not part of the
bootstrap schema. They go into the 'ops' schema.

Run after the bootstrap SQL:
  python migrations/002_add_tracking_tables.py
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
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ops_prod_tracking_rfq ON ops.production_tracking (rfq_id)"))

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

        # Add triggers
        for tbl in ['ops.production_tracking', 'ops.execution_feedback']:
            try:
                conn.execute(text(f"DROP TRIGGER IF EXISTS trg_set_updated_at ON {tbl}"))
                conn.execute(text(f"CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON {tbl} FOR EACH ROW EXECUTE FUNCTION ops.set_updated_at()"))
            except Exception:
                pass

    logger.info("Migration 002 complete.")


if __name__ == "__main__":
    run()
