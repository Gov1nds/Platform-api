"""007 phase2b batch5 evidence ops

Add evidence coverage facts and backlog routing tables for Phase 2B Batch 5.

Revision ID: 007_phase2b_batch5_evidence_ops
Revises: 006_phase2b_batch4_vendor_ingestion_scale
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007_phase2b_batch5_evidence_ops"
down_revision = "006_phase2b_batch4_vendor_ingestion_scale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bom_line_evidence_coverage_facts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("taxonomy_code", sa.Text(), nullable=False, server_default="unclassified"),
        sa.Column("lines_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_sku_mapping", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_fresh_offer", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_fresh_availability", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_hs6", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_tariff_row", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_with_lane_band", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_award_ready", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_rfq_first", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("snapshot_date", "tenant_id", "project_id", "taxonomy_code", name="uq_bom_line_evidence_coverage_fact_dim"),
        schema="ops",
    )
    op.create_index("ix_blecf_snapshot_date", "bom_line_evidence_coverage_facts", ["snapshot_date"], schema="ops")
    op.create_index("ix_blecf_tenant_project", "bom_line_evidence_coverage_facts", ["tenant_id", "project_id"], schema="ops")
    op.create_index("ix_blecf_taxonomy_code", "bom_line_evidence_coverage_facts", ["taxonomy_code"], schema="ops")

    op.create_table(
        "evidence_gap_backlog_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("taxonomy_code", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("priority_score", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("detail_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dedupe_key", name="uq_evidence_gap_backlog_dedupe_key"),
        schema="ops",
    )
    op.create_index("ix_egbi_tenant_status", "evidence_gap_backlog_items", ["tenant_id", "status"], schema="ops")
    op.create_index("ix_egbi_project_category", "evidence_gap_backlog_items", ["project_id", "category"], schema="ops")
    op.create_index("ix_egbi_bom_part", "evidence_gap_backlog_items", ["bom_part_id"], schema="ops")
    op.create_index("ix_egbi_priority", "evidence_gap_backlog_items", ["priority_score", "last_seen_at"], schema="ops")


def downgrade() -> None:
    op.drop_index("ix_egbi_priority", table_name="evidence_gap_backlog_items", schema="ops")
    op.drop_index("ix_egbi_bom_part", table_name="evidence_gap_backlog_items", schema="ops")
    op.drop_index("ix_egbi_project_category", table_name="evidence_gap_backlog_items", schema="ops")
    op.drop_index("ix_egbi_tenant_status", table_name="evidence_gap_backlog_items", schema="ops")
    op.drop_table("evidence_gap_backlog_items", schema="ops")

    op.drop_index("ix_blecf_taxonomy_code", table_name="bom_line_evidence_coverage_facts", schema="ops")
    op.drop_index("ix_blecf_tenant_project", table_name="bom_line_evidence_coverage_facts", schema="ops")
    op.drop_index("ix_blecf_snapshot_date", table_name="bom_line_evidence_coverage_facts", schema="ops")
    op.drop_table("bom_line_evidence_coverage_facts", schema="ops")