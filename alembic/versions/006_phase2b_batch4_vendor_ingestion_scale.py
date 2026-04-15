"""006 phase2b batch4 vendor ingestion scale

Add staged vendor import, vendor identity aliasing, and minimal vendor evidence
attachment support for Phase 2B Batch 4.

Revision ID: 006_phase2b_batch4_vendor_ingestion_scale
Revises: 005_phase2b_batch3_lane_scope_expansion
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006_phase2b_batch4_vendor_ingestion_scale"
down_revision = "005_phase2b_batch3_lane_scope_expansion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False, server_default="buyer_approved_list"),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="staged"),
        sa.Column("import_mode", sa.String(length=40), nullable=False, server_default="upsert_safe"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_collision_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "source_type", "source_ref", name="uq_vendor_import_batch_source_ref"),
        schema="pricing",
    )
    op.create_index("ix_vendor_import_batch_org", "vendor_import_batches", ["organization_id"], schema="pricing")
    op.create_index("ix_vendor_import_batch_status", "vendor_import_batches", ["status"], schema="pricing")

    op.create_table(
        "vendor_import_rows",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="staged"),
        sa.Column("resolution_status", sa.String(length=40), nullable=False, server_default="unresolved"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("raw_row", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("normalized_identity", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("parse_errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_confidence_tier", sa.String(length=40), nullable=True),
        sa.Column("matched_vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolution_confidence", sa.Numeric(12, 6), nullable=True),
        sa.Column("collision_group_key", sa.String(length=160), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "row_index", name="uq_vendor_import_row_index"),
        sa.UniqueConstraint("batch_id", "idempotency_key", name="uq_vendor_import_row_idempotency"),
        schema="pricing",
    )
    op.create_index("ix_vendor_import_row_batch", "vendor_import_rows", ["batch_id"], schema="pricing")
    op.create_index("ix_vendor_import_row_status", "vendor_import_rows", ["status"], schema="pricing")
    op.create_index("ix_vendor_import_row_resolution", "vendor_import_rows", ["resolution_status"], schema="pricing")

    op.create_table(
        "vendor_identity_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_type", sa.String(length=40), nullable=False),
        sa.Column("alias_value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("provenance", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("source_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_import_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_row_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_import_rows.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("alias_type", "normalized_value", "vendor_id", name="uq_vendor_identity_alias_vendor_value"),
        schema="pricing",
    )
    op.create_index("ix_vendor_identity_alias_vendor", "vendor_identity_aliases", ["vendor_id"], schema="pricing")
    op.create_index("ix_vendor_identity_alias_lookup", "vendor_identity_aliases", ["alias_type", "normalized_value"], schema="pricing")
    op.create_index("ix_vendor_identity_alias_batch_row", "vendor_identity_aliases", ["source_batch_id", "source_row_id"], schema="pricing")

    op.create_table(
        "vendor_evidence_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(length=40), nullable=False, server_default="source_confidence"),
        sa.Column("capability_key", sa.String(length=120), nullable=True),
        sa.Column("certification_name", sa.String(length=160), nullable=True),
        sa.Column("source_confidence", sa.Numeric(12, 6), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("source_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_import_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_row_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_import_rows.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evidence_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vendor_evidence_vendor", "vendor_evidence_attachments", ["vendor_id"], schema="pricing")
    op.create_index("ix_vendor_evidence_type", "vendor_evidence_attachments", ["evidence_type"], schema="pricing")
    op.create_index("ix_vendor_evidence_batch_row", "vendor_evidence_attachments", ["source_batch_id", "source_row_id"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_vendor_evidence_batch_row", table_name="vendor_evidence_attachments", schema="pricing")
    op.drop_index("ix_vendor_evidence_type", table_name="vendor_evidence_attachments", schema="pricing")
    op.drop_index("ix_vendor_evidence_vendor", table_name="vendor_evidence_attachments", schema="pricing")
    op.drop_table("vendor_evidence_attachments", schema="pricing")

    op.drop_index("ix_vendor_identity_alias_batch_row", table_name="vendor_identity_aliases", schema="pricing")
    op.drop_index("ix_vendor_identity_alias_lookup", table_name="vendor_identity_aliases", schema="pricing")
    op.drop_index("ix_vendor_identity_alias_vendor", table_name="vendor_identity_aliases", schema="pricing")
    op.drop_table("vendor_identity_aliases", schema="pricing")

    op.drop_index("ix_vendor_import_row_resolution", table_name="vendor_import_rows", schema="pricing")
    op.drop_index("ix_vendor_import_row_status", table_name="vendor_import_rows", schema="pricing")
    op.drop_index("ix_vendor_import_row_batch", table_name="vendor_import_rows", schema="pricing")
    op.drop_table("vendor_import_rows", schema="pricing")

    op.drop_index("ix_vendor_import_batch_status", table_name="vendor_import_batches", schema="pricing")
    op.drop_index("ix_vendor_import_batch_org", table_name="vendor_import_batches", schema="pricing")
    op.drop_table("vendor_import_batches", schema="pricing")
