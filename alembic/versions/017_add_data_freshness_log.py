"""Add data_freshness_log (Blueprint §21.9).
Revision ID: 017
Revises: 016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "017"
down_revision = "016"

def upgrade():
    op.create_table(
        "data_freshness_log",
        sa.Column("log_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("record_id", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("source_api", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("previous_value_json", JSONB, nullable=True),
        sa.Column("new_value_json", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_dfl_table_record_fetched", "data_freshness_log",
                    ["table_name", "record_id", sa.text("fetched_at DESC")])
    op.create_index("ix_dfl_source_status", "data_freshness_log",
                    ["source_api", "status", sa.text("fetched_at DESC")])
    op.execute("CREATE INDEX ix_dfl_recent ON data_freshness_log (fetched_at DESC) WHERE fetched_at > NOW() - INTERVAL '7 days'")

def downgrade():
    op.drop_index("ix_dfl_recent", table_name="data_freshness_log")
    op.drop_index("ix_dfl_source_status", table_name="data_freshness_log")
    op.drop_index("ix_dfl_table_record_fetched", table_name="data_freshness_log")
    op.drop_table("data_freshness_log")
