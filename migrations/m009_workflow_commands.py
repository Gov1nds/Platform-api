"""
Migration 009: workflow command ledger for idempotency and audit.

Run:
  python migrations/m009_workflow_commands.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_009")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects.workflow_commands (
                id uuid PRIMARY KEY,
                namespace text NOT NULL,
                idempotency_key text NOT NULL,
                payload_hash text NOT NULL,
                request_method text NOT NULL,
                request_path text NOT NULL,
                user_id uuid,
                project_id uuid,
                related_id text,
                status text NOT NULL DEFAULT 'processing',
                response_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                error_text text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_commands_namespace_key
            ON projects.workflow_commands (namespace, idempotency_key)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_workflow_commands_project_id
            ON projects.workflow_commands (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_workflow_commands_user_id
            ON projects.workflow_commands (user_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_workflow_commands_status
            ON projects.workflow_commands (status)
        """))

    logger.info("Migration 009 complete.")


if __name__ == "__main__":
    run()