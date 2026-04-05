"""add FK constraints and indexes for canonical lineage pointers

Revision ID: 0002_fk_constraints
Revises: 0001_baseline
Create Date: 2026-04-04

P-4: Add FK constraints for Project canonical workflow pointers.
DB-2: Add FK constraints for BOM.project_id and AnalysisResult.project_id.
DB-3: Add indexes for workflow pointer fields and audit columns.
DB-4: Add scheduler execution columns to ReportSchedule.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_fk_constraints"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _safe_add_column(table, column_name, column_type, schema=None, **kwargs):
    """Add column only if it doesn't already exist."""
    full_table = f"{schema}.{table}" if schema else table
    try:
        op.add_column(full_table, sa.Column(column_name, column_type, **kwargs))
    except Exception:
        pass  # column already exists


def _safe_create_index(index_name, table, columns, schema=None):
    """Create index only if it doesn't already exist."""
    try:
        op.create_index(index_name, table, columns, schema=schema)
    except Exception:
        pass


def _safe_create_fk(constraint_name, source_table, referent_table, local_cols, remote_cols,
                     source_schema=None, referent_schema=None, ondelete="SET NULL"):
    """Create FK only if it doesn't already exist."""
    try:
        op.create_foreign_key(
            constraint_name,
            source_table, referent_table,
            local_cols, remote_cols,
            source_schema=source_schema,
            referent_schema=referent_schema,
            ondelete=ondelete,
        )
    except Exception:
        pass


def upgrade() -> None:
    # ══════════════════════════════════════════════════════════
    # P-4: FK constraints for Project canonical workflow pointers
    # ══════════════════════════════════════════════════════════

    _safe_create_fk(
        "fk_projects_current_analysis",
        "projects", "analysis_results",
        ["current_analysis_id"], ["id"],
        source_schema="projects", referent_schema="bom",
    )
    _safe_create_fk(
        "fk_projects_current_strategy_run",
        "projects", "strategy_runs",
        ["current_strategy_run_id"], ["id"],
        source_schema="projects", referent_schema="bom",
    )
    _safe_create_fk(
        "fk_projects_current_vendor_match",
        "projects", "vendor_match_runs",
        ["current_vendor_match_id"], ["id"],
        source_schema="projects", referent_schema="pricing",
    )
    _safe_create_fk(
        "fk_projects_current_rfq",
        "projects", "rfq_batches",
        ["current_rfq_id"], ["id"],
        source_schema="projects", referent_schema="sourcing",
    )
    _safe_create_fk(
        "fk_projects_current_quote",
        "projects", "rfq_quote_headers",
        ["current_quote_id"], ["id"],
        source_schema="projects", referent_schema="sourcing",
    )
    _safe_create_fk(
        "fk_projects_current_po",
        "projects", "purchase_orders",
        ["current_po_id"], ["id"],
        source_schema="projects", referent_schema="ops",
    )
    _safe_create_fk(
        "fk_projects_current_shipment",
        "projects", "shipments",
        ["current_shipment_id"], ["id"],
        source_schema="projects", referent_schema="ops",
    )
    _safe_create_fk(
        "fk_projects_current_invoice",
        "projects", "invoices",
        ["current_invoice_id"], ["id"],
        source_schema="projects", referent_schema="ops",
    )

    # ══════════════════════════════════════════════════════════
    # DB-2: FK constraints for BOM.project_id and AnalysisResult.project_id
    # ══════════════════════════════════════════════════════════

    _safe_create_fk(
        "fk_boms_project",
        "boms", "projects",
        ["project_id"], ["id"],
        source_schema="bom", referent_schema="projects",
    )
    _safe_create_fk(
        "fk_analysis_results_project",
        "analysis_results", "projects",
        ["project_id"], ["id"],
        source_schema="bom", referent_schema="projects",
    )

    # ══════════════════════════════════════════════════════════
    # DB-3: Indexes for workflow pointer columns
    # ══════════════════════════════════════════════════════════

    _safe_create_index("ix_projects_current_rfq", "projects", ["current_rfq_id"], schema="projects")
    _safe_create_index("ix_projects_current_po", "projects", ["current_po_id"], schema="projects")
    _safe_create_index("ix_projects_current_shipment", "projects", ["current_shipment_id"], schema="projects")
    _safe_create_index("ix_projects_current_invoice", "projects", ["current_invoice_id"], schema="projects")
    _safe_create_index("ix_projects_workflow_stage", "projects", ["workflow_stage"], schema="projects")

    # Workflow command audit indexes
    _safe_create_index("ix_workflow_commands_project", "workflow_commands", ["project_id"], schema="ops")
    _safe_create_index("ix_workflow_commands_namespace", "workflow_commands", ["namespace"], schema="ops")

    # ══════════════════════════════════════════════════════════
    # DB-4: Scheduler execution columns on ReportSchedule
    # ══════════════════════════════════════════════════════════

    _safe_add_column("report_schedules", "last_run_status", sa.Text(), schema="analytics", nullable=True)
    _safe_add_column("report_schedules", "last_run_error", sa.Text(), schema="analytics", nullable=True)
    _safe_add_column("report_schedules", "job_correlation_id", sa.Text(), schema="analytics", nullable=True)
    _safe_add_column("report_schedules", "total_runs", sa.Integer(), schema="analytics", nullable=False, server_default="0")
    _safe_add_column("report_schedules", "consecutive_failures", sa.Integer(), schema="analytics", nullable=False, server_default="0")
    _safe_create_index("ix_report_schedules_next_run", "report_schedules", ["next_run_at"], schema="analytics")


def downgrade() -> None:
    # Drop indexes (safe)
    for idx in [
        ("ix_projects_current_rfq", "projects", "projects"),
        ("ix_projects_current_po", "projects", "projects"),
        ("ix_projects_current_shipment", "projects", "projects"),
        ("ix_projects_current_invoice", "projects", "projects"),
        ("ix_projects_workflow_stage", "projects", "projects"),
        ("ix_workflow_commands_project", "workflow_commands", "ops"),
        ("ix_workflow_commands_namespace", "workflow_commands", "ops"),
        ("ix_report_schedules_next_run", "report_schedules", "analytics"),
    ]:
        try:
            op.drop_index(idx[0], table_name=idx[1], schema=idx[2])
        except Exception:
            pass

    # Drop scheduler columns
    for col in ["last_run_status", "last_run_error", "job_correlation_id", "total_runs", "consecutive_failures"]:
        try:
            op.drop_column("report_schedules", col, schema="analytics")
        except Exception:
            pass

    # Drop FK constraints
    fk_names = [
        ("fk_projects_current_analysis", "projects", "projects"),
        ("fk_projects_current_strategy_run", "projects", "projects"),
        ("fk_projects_current_vendor_match", "projects", "projects"),
        ("fk_projects_current_rfq", "projects", "projects"),
        ("fk_projects_current_quote", "projects", "projects"),
        ("fk_projects_current_po", "projects", "projects"),
        ("fk_projects_current_shipment", "projects", "projects"),
        ("fk_projects_current_invoice", "projects", "projects"),
        ("fk_boms_project", "boms", "bom"),
        ("fk_analysis_results_project", "analysis_results", "bom"),
    ]
    for name, table, schema in fk_names:
        try:
            op.drop_constraint(name, table, schema=schema, type_="foreignkey")
        except Exception:
            pass
