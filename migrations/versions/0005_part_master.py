"""0005 — intelligence.part_master with pgvector embedding and tsvector.

Revision ID: 0005
Revises: 0004
Create Date: 2024-01-01

Contract anchors:
  §2.8 Part_Master — canonical component catalog
  Indexes: GIN on search_tokens, IVFFlat/HNSW on embedding, commodity_group, category.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTEL = "intelligence"


def upgrade() -> None:
    op.create_table(
        "part_master",
        sa.Column(
            "part_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.String(512), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("commodity_group", sa.String(128), nullable=False),
        sa.Column("taxonomy_code", sa.String(64), nullable=True),
        sa.Column(
            "spec_template",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "default_uom",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pc'"),
        ),
        # search_tokens TSVECTOR
        sa.Column(
            "search_tokens",
            sa.Column("search_tokens", sa.Text).__class__,  # placeholder; real type set via DDL
            nullable=True,
        ),
        # embedding VECTOR(1536) — pgvector
        sa.Column("classification_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "classification_confidence BETWEEN 0 AND 1 OR classification_confidence IS NULL",
            name="ck_part_master_classification_confidence_range",
        ),
        schema=INTEL,
    )

    # Replace placeholder column with real TSVECTOR and add VECTOR using raw DDL
    op.execute(
        "ALTER TABLE intelligence.part_master "
        "ALTER COLUMN search_tokens TYPE TSVECTOR "
        "USING search_tokens::TSVECTOR"
    )
    op.execute(
        "ALTER TABLE intelligence.part_master "
        "ADD COLUMN embedding VECTOR(1536)"
    )

    # GIN index on search_tokens
    op.execute(
        "CREATE INDEX ix_part_master_search_tokens_gin "
        "ON intelligence.part_master USING GIN (search_tokens)"
    )
    # HNSW index on embedding (requires pgvector)
    op.execute(
        "CREATE INDEX ix_part_master_embedding_hnsw "
        "ON intelligence.part_master USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.create_index(
        "ix_part_master_commodity_group",
        "part_master",
        ["commodity_group"],
        schema=INTEL,
    )
    op.create_index(
        "ix_part_master_category", "part_master", ["category"], schema=INTEL
    )


def downgrade() -> None:
    op.drop_table("part_master", schema=INTEL)