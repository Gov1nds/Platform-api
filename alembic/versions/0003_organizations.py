"""add organization workspace membership tables

Revision ID: 0003_organizations
Revises: 0002_fk_constraints
Create Date: 2026-04-04

P-3/DB-1: Adds org/workspace/membership tables for team-based procurement.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

revision = "0003_organizations"
down_revision = "0002_fk_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text('CREATE SCHEMA IF NOT EXISTS "orgs"'))

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("default_currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("default_region", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="orgs",
    )
    op.create_index("ix_organizations_status", "organizations", ["status"], schema="orgs")
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True, schema="orgs")

    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("orgs.organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("default_currency", sa.Text(), nullable=True),
        sa.Column("default_region", sa.Text(), nullable=True),
        sa.Column("budget_limit", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="orgs",
    )
    op.create_index("ix_workspaces_org", "workspaces", ["organization_id"], schema="orgs")
    op.create_index("ix_workspaces_status", "workspaces", ["status"], schema="orgs")

    op.create_table(
        "workspace_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("orgs.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("invited_email", sa.Text(), nullable=True),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="orgs",
    )
    op.create_index("ix_ws_memberships_workspace", "workspace_memberships", ["workspace_id"], schema="orgs")
    op.create_index("ix_ws_memberships_user", "workspace_memberships", ["user_id"], schema="orgs")
    op.create_index("ix_ws_memberships_status", "workspace_memberships", ["status"], schema="orgs")


def downgrade() -> None:
    op.drop_table("workspace_memberships", schema="orgs")
    op.drop_table("workspaces", schema="orgs")
    op.drop_table("organizations", schema="orgs")
