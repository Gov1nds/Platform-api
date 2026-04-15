"""005 phase2b batch3 lane scope expansion

Add Phase 2B Batch 3 lane scope registry and broader lane/service coverage metadata.

Revision ID: 005_phase2b_batch3_lane_scope_expansion
Revises: 004_phase2b_batch2_tariff_scope_expansion
Create Date: 2026-04-15 09:15:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005_phase2b_batch3_lane_scope_expansion"
down_revision = "004_phase2b_batch2_tariff_scope_expansion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lane_scope_registry",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("lane_key", sa.String(length=255), nullable=False),
        sa.Column("origin_country", sa.String(length=3), nullable=False),
        sa.Column("origin_region", sa.Text(), nullable=True),
        sa.Column("destination_country", sa.String(length=3), nullable=False),
        sa.Column("destination_region", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="sea"),
        sa.Column("service_level", sa.String(length=40), nullable=True),
        sa.Column("scope_status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("priority_tier", sa.String(length=20), nullable=False, server_default="standard"),
        sa.Column("refresh_cadence", sa.String(length=40), nullable=True),
        sa.Column("activity_score", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("lane_key", name="uq_lane_scope_registry_lane_key"),
        schema="market",
    )
    op.create_index("ix_lane_scope_registry_lane_key", "lane_scope_registry", ["lane_key"], schema="market")
    op.create_index(
        "ix_lane_scope_registry_priority",
        "lane_scope_registry",
        ["priority_tier", "last_used_at"],
        schema="market",
    )
    op.create_index(
        "ix_lane_scope_registry_refresh",
        "lane_scope_registry",
        ["refresh_cadence", "last_refreshed_at"],
        schema="market",
    )

    op.add_column("lane_rate_bands", sa.Column("service_level", sa.String(length=40), nullable=True), schema="market")
    op.create_index(
        "ix_lane_rate_bands_service_level",
        "lane_rate_bands",
        ["service_level"],
        schema="market",
    )


def downgrade() -> None:
    op.drop_index("ix_lane_rate_bands_service_level", table_name="lane_rate_bands", schema="market")
    op.drop_column("lane_rate_bands", "service_level", schema="market")

    op.drop_index("ix_lane_scope_registry_refresh", table_name="lane_scope_registry", schema="market")
    op.drop_index("ix_lane_scope_registry_priority", table_name="lane_scope_registry", schema="market")
    op.drop_index("ix_lane_scope_registry_lane_key", table_name="lane_scope_registry", schema="market")
    op.drop_table("lane_scope_registry", schema="market")
