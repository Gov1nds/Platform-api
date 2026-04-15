"""008 phase2c batch2c1 outcome ingestion and vendor scorecard foundation

Revision ID: 008_phase2c_batch2c1_outcome_ingestion
Revises: 007_phase2b_batch5_evidence_ops
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_phase2c_batch2c1_outcome_ingestion"
down_revision = "007_phase2b_batch5_evidence_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quoted_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("quoted_lead_time", sa.Numeric(12, 2), nullable=True),
        sa.Column("is_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("accepted_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("accepted_lead_time", sa.Numeric(12, 2), nullable=True),
        sa.Column("quote_date", sa.Date(), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("issues_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_quote_outcomes_bom_line_vendor", "quote_outcomes", ["bom_line_id", "vendor_id"], schema="pricing")
    op.create_index("ix_quote_outcomes_vendor_date", "quote_outcomes", ["vendor_id", "quote_date"], schema="pricing")
    op.create_index("ix_quote_outcomes_accepted", "quote_outcomes", ["is_accepted"], schema="pricing")

    op.create_table(
        "override_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommended_vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("chosen_vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("override_reason_code", sa.String(length=80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_override_events_event_id"),
        schema="ops",
    )
    op.create_index("ix_override_events_bom_line_timestamp", "override_events", ["bom_line_id", "timestamp"], schema="ops")
    op.create_index("ix_override_events_user_timestamp", "override_events", ["user_id", "timestamp"], schema="ops")

    op.create_table(
        "vendor_performance",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("on_time_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("avg_lead_time", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_variance", sa.Numeric(20, 8), nullable=True),
        sa.Column("po_win_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("vendor_id", "period_start", "period_end", name="uq_vendor_performance_period"),
        schema="pricing",
    )
    op.create_index("ix_vendor_performance_vendor_period", "vendor_performance", ["vendor_id", "period_start", "period_end"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_vendor_performance_vendor_period", table_name="vendor_performance", schema="pricing")
    op.drop_table("vendor_performance", schema="pricing")

    op.drop_index("ix_override_events_user_timestamp", table_name="override_events", schema="ops")
    op.drop_index("ix_override_events_bom_line_timestamp", table_name="override_events", schema="ops")
    op.drop_table("override_events", schema="ops")

    op.drop_index("ix_quote_outcomes_accepted", table_name="quote_outcomes", schema="pricing")
    op.drop_index("ix_quote_outcomes_vendor_date", table_name="quote_outcomes", schema="pricing")
    op.drop_index("ix_quote_outcomes_bom_line_vendor", table_name="quote_outcomes", schema="pricing")
    op.drop_table("quote_outcomes", schema="pricing")
