"""0001 — Create PostgreSQL extensions and domain schemas.

Revision ID: 0001
Revises: None
Create Date: 2024-01-01

Contract anchors:
  §9.1  Schema isolation strategy (13 schemas).
  §9.3  Required PostgreSQL extensions (uuid-ossp, pgvector, pg_trgm, unaccent).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMAS = [
    "identity",
    "workspace",
    "intelligence",
    "marketplace",
    "market_data",
    "transactions",
    "chat",
    "notifications",
    "guest",
    "audit",
    "analytics",
    "config",
]


def upgrade() -> None:
    # ── Extensions ───────────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgvector"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "unaccent"')

    # ── Schemas ───────────────────────────────────────────────────────────────
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    op.execute('DROP EXTENSION IF EXISTS "unaccent"')
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm"')
    op.execute('DROP EXTENSION IF EXISTS "pgvector"')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')