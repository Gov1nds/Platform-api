"""baseline — capture current schema state

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-03-28
"""
from alembic import op
from sqlalchemy import text

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = [
    "auth", "bom", "projects", "pricing", "sourcing",
    "ops", "geo", "catalog", "collaboration", "analytics", "integrations",
]


def upgrade() -> None:
    conn = op.get_bind()
    for schema in SCHEMAS:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def downgrade() -> None:
    pass
