"""
Migration 006: collaboration tables for chat, attachments, approvals.

Run:
  python migrations/m006_collaboration_tables.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_006")


def run():
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "collaboration"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "projects"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "sourcing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "pricing"'))
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS "auth"'))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.chat_threads (
                id uuid PRIMARY KEY,
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                rfq_batch_id uuid REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                created_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                thread_type text NOT NULL DEFAULT 'project',
                title text NOT NULL DEFAULT 'Conversation',
                is_internal_only boolean NOT NULL DEFAULT true,
                status text NOT NULL DEFAULT 'active',
                last_message_at timestamptz,
                last_message_id uuid,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_chat_threads_project
            ON collaboration.chat_threads (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_chat_threads_rfq
            ON collaboration.chat_threads (rfq_batch_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_chat_threads_vendor
            ON collaboration.chat_threads (vendor_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.chat_messages (
                id uuid PRIMARY KEY,
                thread_id uuid NOT NULL REFERENCES collaboration.chat_threads(id) ON DELETE CASCADE,
                sender_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                body text NOT NULL,
                message_type text NOT NULL DEFAULT 'message',
                is_internal_only boolean NOT NULL DEFAULT true,
                reply_to_message_id uuid,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_chat_messages_thread
            ON collaboration.chat_messages (thread_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.message_attachments (
                id uuid PRIMARY KEY,
                message_id uuid NOT NULL REFERENCES collaboration.chat_messages(id) ON DELETE CASCADE,
                uploaded_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                file_name text NOT NULL,
                file_path text NOT NULL,
                mime_type text,
                file_size integer,
                file_url text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.chat_read_receipts (
                id uuid PRIMARY KEY,
                thread_id uuid NOT NULL REFERENCES collaboration.chat_threads(id) ON DELETE CASCADE,
                message_id uuid NOT NULL REFERENCES collaboration.chat_messages(id) ON DELETE CASCADE,
                user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                read_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_chat_receipts_thread_user
            ON collaboration.chat_read_receipts (thread_id, user_id)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.approval_requests (
                id uuid PRIMARY KEY,
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                thread_id uuid REFERENCES collaboration.chat_threads(id) ON DELETE SET NULL,
                rfq_batch_id uuid REFERENCES sourcing.rfq_batches(id) ON DELETE SET NULL,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                requested_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                assigned_to_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                required_role text NOT NULL DEFAULT 'manager',
                title text NOT NULL,
                description text,
                status text NOT NULL DEFAULT 'pending',
                due_at timestamptz,
                resolved_at timestamptz,
                resolution_note text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_approval_requests_project
            ON collaboration.approval_requests (project_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_approval_requests_thread
            ON collaboration.approval_requests (thread_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_approval_requests_status
            ON collaboration.approval_requests (status)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS collaboration.approval_actions (
                id uuid PRIMARY KEY,
                approval_request_id uuid NOT NULL REFERENCES collaboration.approval_requests(id) ON DELETE CASCADE,
                acted_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                action text NOT NULL,
                note text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))

    logger.info("Migration 006 complete.")


if __name__ == "__main__":
    run()