"""013 phase3 part vendor index

Revision ID: 013_phase3_part_vendor_index
Revises: 012_phase3_vendor_intelligence_model
Create Date: 2026-04-16 00:10:00.000000

Adds pricing.part_vendor_index: the evidence index linking canonical parts
to vendors with historical quote/PO signals and award-ready classification.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "013_phase3_part_vendor_index"
down_revision = "012_phase3_vendor_intelligence_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "part_vendor_index",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=False),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.String(length=40), nullable=False, server_default="partial_category"),
        sa.Column("match_score", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_quote_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_quote_currency", sa.String(length=3), nullable=True),
        sa.Column("last_quote_date", sa.Date(), nullable=True),
        sa.Column("last_po_date", sa.Date(), nullable=True),
        sa.Column("po_win_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rfq_sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("award_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rfq_first_recommended", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("category_match_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("material_match_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("process_match_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("alias_match_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("historical_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_part_key", "vendor_id", name="uq_part_vendor_index_part_vendor"),
        schema="pricing",
    )
    op.create_index("ix_part_vendor_index_part", "part_vendor_index", ["canonical_part_key"], schema="pricing")
    op.create_index("ix_part_vendor_index_vendor", "part_vendor_index", ["vendor_id"], schema="pricing")
    op.create_index("ix_part_vendor_index_award", "part_vendor_index", ["award_ready"], schema="pricing")
    op.create_index("ix_part_vendor_index_score", "part_vendor_index", ["match_score"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_part_vendor_index_score", table_name="part_vendor_index", schema="pricing")
    op.drop_index("ix_part_vendor_index_award", table_name="part_vendor_index", schema="pricing")
    op.drop_index("ix_part_vendor_index_vendor", table_name="part_vendor_index", schema="pricing")
    op.drop_index("ix_part_vendor_index_part", table_name="part_vendor_index", schema="pricing")
    op.drop_table("part_vendor_index", schema="pricing")
