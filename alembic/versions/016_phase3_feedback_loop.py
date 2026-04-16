"""016 phase3 feedback loop

Revision ID: 016_phase3_feedback_loop
Revises: 015_phase3_market_intelligence
Create Date: 2026-04-16 00:40:00.000000

Adds Phase 3 continuous learning + feedback loop tables:
  - pricing.recommendation_overrides (user overrides of system picks)
  - pricing.learning_events (audit trail of score adjustments / aliases /
    demotions / confidence updates, with human-review gates)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "016_phase3_feedback_loop"
down_revision = "015_phase3_market_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── recommendation_overrides ───────────────────────────────────────────
    op.create_table(
        "recommendation_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("projects.projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("recommended_vendor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("override_vendor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("strategy_at_time", sa.String(length=40), nullable=True),
        sa.Column("score_at_time", sa.Numeric(6, 4), nullable=True),
        sa.Column("override_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index(
        "ix_recommendation_overrides_project",
        "recommendation_overrides",
        ["project_id"],
        schema="pricing",
    )
    op.create_index(
        "ix_recommendation_overrides_recommended_vendor",
        "recommendation_overrides",
        ["recommended_vendor_id"],
        schema="pricing",
    )

    # ── learning_events ────────────────────────────────────────────────────
    op.create_table(
        "learning_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=60), nullable=False, server_default="scheduled_recompute"),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_count_at_time", sa.Integer(), nullable=True),
        sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("human_review_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index(
        "ix_learning_events_vendor_type",
        "learning_events",
        ["vendor_id", "event_type"],
        schema="pricing",
    )
    op.create_index(
        "ix_learning_events_review_required",
        "learning_events",
        ["human_review_required", "created_at"],
        schema="pricing",
    )


def downgrade() -> None:
    op.drop_index("ix_learning_events_review_required", table_name="learning_events", schema="pricing")
    op.drop_index("ix_learning_events_vendor_type", table_name="learning_events", schema="pricing")
    op.drop_table("learning_events", schema="pricing")

    op.drop_index("ix_recommendation_overrides_recommended_vendor", table_name="recommendation_overrides", schema="pricing")
    op.drop_index("ix_recommendation_overrides_project", table_name="recommendation_overrides", schema="pricing")
    op.drop_table("recommendation_overrides", schema="pricing")
