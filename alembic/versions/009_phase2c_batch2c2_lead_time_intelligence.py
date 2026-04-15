"""009 phase2c batch2c2 vendor performance computation and lead-time intelligence

Revision ID: 009_phase2c_batch2c2_lead_time_intelligence
Revises: 008_phase2c_batch2c1_outcome_ingestion
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_phase2c_batch2c2_lead_time_intelligence"
down_revision = "008_phase2c_batch2c1_outcome_ingestion"
branch_labels = None
depends_on = None



def upgrade() -> None:
    op.create_table(
        "lead_time_history",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("quote_outcome_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.quote_outcomes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quoted_lead_time", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_lead_time", sa.Numeric(12, 2), nullable=False),
        sa.Column("lead_time_diff_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("quote_outcome_id", name="uq_lead_time_history_quote_outcome"),
        schema="pricing",
    )
    op.create_index("ix_lead_time_history_vendor_recorded", "lead_time_history", ["vendor_id", "recorded_at"], schema="pricing")
    op.create_index("ix_lead_time_history_vendor_bom", "lead_time_history", ["vendor_id", "bom_line_id"], schema="pricing")

    op.add_column("vendor_performance", sa.Column("lead_time_variance", sa.Numeric(12, 4), nullable=True), schema="pricing")



def downgrade() -> None:
    op.drop_column("vendor_performance", "lead_time_variance", schema="pricing")

    op.drop_index("ix_lead_time_history_vendor_bom", table_name="lead_time_history", schema="pricing")
    op.drop_index("ix_lead_time_history_vendor_recorded", table_name="lead_time_history", schema="pricing")
    op.drop_table("lead_time_history", schema="pricing")