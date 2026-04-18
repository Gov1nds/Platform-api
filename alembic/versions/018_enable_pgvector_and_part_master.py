"""Enable pgvector; ensure Part_Master (Blueprint §6.1, §21.2).
Revision ID: 018
Revises: 017
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "018"
down_revision = "017"

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn = op.get_bind()
    exists = conn.execute(sa.text("SELECT to_regclass('public.part_master')")).scalar()
    if exists is None:
        op.create_table(
            "part_master",
            sa.Column("part_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("canonical_name", sa.Text, nullable=False),
            sa.Column("category", sa.Text, nullable=False, server_default="unknown"),
            sa.Column("commodity_group", sa.Text, nullable=True),
            sa.Column("taxonomy_code", sa.Text, nullable=True),
            sa.Column("spec_template", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("default_uom", sa.Text, nullable=True),
            sa.Column("search_tokens", sa.Text, nullable=True),
            sa.Column("classification_confidence", sa.Numeric(5, 4), nullable=True),
            sa.Column("manufacturer_part_number", sa.Text, nullable=True),
            sa.Column("manufacturer", sa.Text, nullable=True),
            sa.Column("canonical_sku_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        )
        op.create_index("ix_part_master_mpn", "part_master", ["manufacturer_part_number"])
        op.create_index("ix_part_master_commodity", "part_master", ["commodity_group"])
        op.execute("""
            CREATE INDEX ix_part_master_search ON part_master
            USING GIN (to_tsvector('english', canonical_name || ' ' || COALESCE(manufacturer, '')))
        """)
    op.execute("ALTER TABLE part_master ADD COLUMN IF NOT EXISTS embedding vector(384)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_part_master_embedding_hnsw
        ON part_master USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_part_master_embedding_hnsw")
    op.execute("ALTER TABLE part_master DROP COLUMN IF EXISTS embedding")
