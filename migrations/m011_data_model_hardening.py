"""
Migration 011: data model hardening for guest lineage, lifecycle flags,
participant access, vendor operational data, quote normalization, and fulfillment lineage.

Run:
  python migrations/m011_data_model_hardening.py
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration_011")


def _exec(conn, sql: str):
    conn.execute(text(sql))


def _add_column(conn, table: str, column_sql: str):
    _exec(conn, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_sql}")


def _add_index(conn, index_sql: str):
    _exec(conn, index_sql)


def _add_fk_not_valid(conn, ddl: str):
    _exec(conn, ddl)


def run():
    with engine.begin() as conn:
        # Schemas
        for schema in ("auth", "bom", "projects", "pricing", "sourcing", "ops", "collaboration"):
            _exec(conn, f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        # ------------------------------------------------------------
        # Guest intake lineage
        # ------------------------------------------------------------
        _add_column(conn, "projects.intake_sessions", "guest_session_id varchar(36)")
        _add_column(conn, "projects.intake_sessions", "session_token varchar(120)")
        _add_column(conn, "projects.intake_sessions", "idempotency_key varchar(120) NOT NULL DEFAULT ''")
        _add_column(conn, "projects.intake_sessions", "request_hash varchar(128) NOT NULL DEFAULT ''")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_intake_sessions_session_token ON projects.intake_sessions (session_token)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_intake_sessions_idempotency_key ON projects.intake_sessions (idempotency_key)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_intake_sessions_request_hash ON projects.intake_sessions (request_hash)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_intake_sessions_guest_session_id ON projects.intake_sessions (guest_session_id)")
        _add_fk_not_valid(conn, """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'fk_intake_sessions_guest_session'
                ) THEN
                    ALTER TABLE projects.intake_sessions
                    ADD CONSTRAINT fk_intake_sessions_guest_session
                    FOREIGN KEY (guest_session_id)
                    REFERENCES auth.guest_sessions(id)
                    ON DELETE SET NULL
                    NOT VALID;
                END IF;
            END$$;
        """)

        # ------------------------------------------------------------
        # Canonical lifecycle columns on BOM, analysis, and project
        # ------------------------------------------------------------
        for table in ("bom.boms", "bom.analysis_results", "projects.projects"):
            _add_column(conn, table, "analysis_status varchar(40) NOT NULL DEFAULT 'guest_preview'")
            _add_column(conn, table, "report_visibility_level varchar(40) NOT NULL DEFAULT 'preview'")
            _add_column(conn, table, "unlock_status varchar(40) NOT NULL DEFAULT 'locked'")
            _add_column(conn, table, "workspace_route text")

        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_boms_analysis_status ON bom.boms (analysis_status)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_boms_report_visibility_level ON bom.boms (report_visibility_level)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_analysis_results_analysis_status ON bom.analysis_results (analysis_status)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_analysis_results_report_visibility_level ON bom.analysis_results (report_visibility_level)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_projects_analysis_status ON projects.projects (analysis_status)")
        _add_index(conn, "CREATE INDEX IF NOT EXISTS ix_projects_visibility_level ON projects.projects (visibility_level)")

        # ------------------------------------------------------------
        # Project ownership / access semantics
        # ------------------------------------------------------------
        _exec(conn, """
            CREATE TABLE IF NOT EXISTS projects.project_participants (
                id uuid PRIMARY KEY,
                project_id uuid NOT NULL REFERENCES projects.projects(id) ON DELETE CASCADE,
                user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                vendor_id uuid REFERENCES pricing.vendors(id) ON DELETE SET NULL,
                invited_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
                approval_request_id uuid REFERENCES collaboration.approval_requests(id) ON DELETE SET NULL,
                participant_type text NOT NULL DEFAULT 'collaborator',
                access_level text NOT NULL DEFAULT 'read',
                status text NOT NULL DEFAULT 'invited',
                invited_email text,
                accepted_at timestamptz,
                revoked_at timestamptz,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_project_participants_project ON projects.project_participants (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_project_participants_user ON projects.project_participants (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_project_participants_vendor ON projects.project_participants (vendor_id)",
            "CREATE INDEX IF NOT EXISTS ix_project_participants_type ON projects.project_participants (participant_type)",
            "CREATE INDEX IF NOT EXISTS ix_project_participants_status ON projects.project_participants (status)",
            "CREATE INDEX IF NOT EXISTS ix_project_participants_approval ON projects.project_participants (approval_request_id)",
        ):
            _add_index(conn, idx)

        # ------------------------------------------------------------
        # Vendor operational fields
        # ------------------------------------------------------------
        vendor_columns = (
            "default_currency varchar(3) NOT NULL DEFAULT 'USD'",
            "default_moq numeric(18,6)",
            "lead_time_profile jsonb NOT NULL DEFAULT '{}'::jsonb",
            "incoterms jsonb NOT NULL DEFAULT '[]'::jsonb",
            "payment_terms jsonb NOT NULL DEFAULT '[]'::jsonb",
            "regions_served jsonb NOT NULL DEFAULT '[]'::jsonb",
            "certifications jsonb NOT NULL DEFAULT '[]'::jsonb",
            "capacity_profile jsonb NOT NULL DEFAULT '{}'::jsonb",
            "quality_rating numeric(12,6)",
            "logistics_capability jsonb NOT NULL DEFAULT '{}'::jsonb",
            "sample_order_available boolean NOT NULL DEFAULT false",
            "quote_validity_days integer NOT NULL DEFAULT 14",
        )
        for col in vendor_columns:
            _add_column(conn, "pricing.vendors", col)

        # ------------------------------------------------------------
        # Quote normalization fields
        # ------------------------------------------------------------
        quote_columns = (
            "quote_version integer NOT NULL DEFAULT 1",
            "acceptance_status text NOT NULL DEFAULT 'pending'",
            "incoterms text",
            "tax_assumptions jsonb NOT NULL DEFAULT '{}'::jsonb",
            "duty_assumptions jsonb NOT NULL DEFAULT '{}'::jsonb",
            "tier_pricing_json jsonb NOT NULL DEFAULT '{}'::jsonb",
            "line_normalization_source text",
        )
        for col in quote_columns:
            _add_column(conn, "sourcing.rfq_quotes", col)
            _add_column(conn, "sourcing.rfq_quote_headers", col)
        _add_column(conn, "sourcing.rfq_quote_lines", "normalization_source text")

        # ------------------------------------------------------------
        # Fulfillment event lineage
        # ------------------------------------------------------------
        _exec(conn, """
            CREATE TABLE IF NOT EXISTS ops.fulfillment_events (
                id uuid PRIMARY KEY,
                rfq_id uuid REFERENCES sourcing.rfq_batches(id) ON DELETE CASCADE,
                project_id uuid REFERENCES projects.projects(id) ON DELETE CASCADE,
                purchase_order_id uuid REFERENCES ops.purchase_orders(id) ON DELETE SET NULL,
                shipment_id uuid REFERENCES ops.shipments(id) ON DELETE SET NULL,
                invoice_id uuid REFERENCES ops.invoices(id) ON DELETE SET NULL,
                payment_state_id uuid REFERENCES ops.payment_states(id) ON DELETE SET NULL,
                event_type text NOT NULL,
                event_state text,
                source_entity text,
                source_id text,
                context_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                occurred_at timestamptz NOT NULL DEFAULT now(),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        for idx in (
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_rfq ON ops.fulfillment_events (rfq_id)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_project ON ops.fulfillment_events (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_po ON ops.fulfillment_events (purchase_order_id)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_shipment ON ops.fulfillment_events (shipment_id)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_invoice ON ops.fulfillment_events (invoice_id)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_type ON ops.fulfillment_events (event_type)",
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_occurred ON ops.fulfillment_events (occurred_at)",
        ):
            _add_index(conn, idx)

    logger.info("Migration 011 complete.")


if __name__ == "__main__":
    run()
