"""
Migration 010: universal intake sessions and intake items.
Run:
  python migrations/m010_intake_tables.py
"""
from __future__ import annotations

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_010")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects.intake_sessions (
                id varchar(36) PRIMARY KEY,
                namespace varchar(80) NOT NULL DEFAULT 'intake.submit',
                idempotency_key varchar(120) NOT NULL DEFAULT '',
                request_hash varchar(128) NOT NULL DEFAULT '',
                user_id varchar(36),
                guest_session_id varchar(36),
                session_token varchar(120),
                input_type varchar(40) NOT NULL DEFAULT 'auto',
                intent varchar(40) NOT NULL DEFAULT 'auto',
                source_channel varchar(40) NOT NULL DEFAULT 'web',
                raw_input_text text,
                normalized_text text,
                voice_transcript text,
                source_file_name text,
                source_file_type varchar(40),
                source_file_size integer,
                source_file_path text,
                audio_file_name text,
                audio_file_type varchar(80),
                audio_file_size integer,
                audio_file_path text,
                delivery_location varchar(120),
                target_currency varchar(20),
                priority varchar(20) NOT NULL DEFAULT 'cost',
                status varchar(40) NOT NULL DEFAULT 'received',
                parse_status varchar(40) NOT NULL DEFAULT 'pending',
                analysis_status varchar(40) NOT NULL DEFAULT 'pending',
                workflow_status varchar(40) NOT NULL DEFAULT 'received',
                confidence_score double precision NOT NULL DEFAULT 0,
                warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
                suggestions jsonb NOT NULL DEFAULT '[]'::jsonb,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                parsed_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                normalized_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                analysis_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                preview_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                bom_id varchar(36),
                analysis_id varchar(36),
                project_id varchar(36),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_sessions_namespace_key
            ON projects.intake_sessions (namespace, idempotency_key)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_sessions_user_id
            ON projects.intake_sessions (user_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_sessions_guest_session_id
            ON projects.intake_sessions (guest_session_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_sessions_status
            ON projects.intake_sessions (status)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_sessions_input_type
            ON projects.intake_sessions (input_type)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_sessions_project_id
            ON projects.intake_sessions (project_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects.intake_items (
                id varchar(36) PRIMARY KEY,
                session_id varchar(36) NOT NULL REFERENCES projects.intake_sessions(id) ON DELETE CASCADE,
                line_no integer NOT NULL DEFAULT 1,
                raw_text text NOT NULL DEFAULT '',
                item_name text NOT NULL DEFAULT '',
                category varchar(80) NOT NULL DEFAULT 'standard',
                material text,
                process text,
                quantity double precision NOT NULL DEFAULT 1,
                unit varchar(30),
                specs jsonb NOT NULL DEFAULT '{}'::jsonb,
                confidence double precision NOT NULL DEFAULT 0,
                warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
                source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_items_session_id
            ON projects.intake_items (session_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_items_category
            ON projects.intake_items (category)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_intake_items_item_name
            ON projects.intake_items (item_name)
        """))

    logger.info("Migration 010 complete.")


if __name__ == "__main__":
    run()