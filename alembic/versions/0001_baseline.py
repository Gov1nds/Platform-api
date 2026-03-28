"""baseline — capture current schema state

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-03-28

This is the baseline migration. It represents the schema state at the time
Alembic was introduced. All tables already exist in production via init_db()
and the custom migration scripts (m002, m003).

Running this on an existing database is a no-op.
Running this on a fresh database creates all schemas.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ["auth", "bom", "projects", "pricing", "sourcing", "ops", "geo", "catalog"]


def upgrade() -> None:
    """Create schemas if they don't exist. Tables are created by SQLAlchemy
    metadata.create_all() in init_db(). This migration just marks the baseline."""
    conn = op.get_bind()
    for schema in SCHEMAS:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def downgrade() -> None:
    """Cannot safely drop schemas — would destroy all data."""
    pass
