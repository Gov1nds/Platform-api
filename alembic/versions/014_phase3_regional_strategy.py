"""014 phase3 regional strategy

Revision ID: 014_phase3_regional_strategy
Revises: 013_phase3_part_vendor_index
Create Date: 2026-04-16 00:20:00.000000

Adds pricing.regional_strategy_runs: persisted audit of per-project regional
sourcing strategy evaluations (local / regional / national / international).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "014_phase3_regional_strategy"
down_revision = "013_phase3_part_vendor_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regional_strategy_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("projects.projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester_location", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("local_bucket", sa.Text(), nullable=True),
        sa.Column("regional_bucket", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("national_bucket", sa.Text(), nullable=True),
        sa.Column("international_bucket", sa.Text(), nullable=True),
        sa.Column("strategy_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_regional_strategy_runs_project", "regional_strategy_runs", ["project_id"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_regional_strategy_runs_project", table_name="regional_strategy_runs", schema="pricing")
    op.drop_table("regional_strategy_runs", schema="pricing")
