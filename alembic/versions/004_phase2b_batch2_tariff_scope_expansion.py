"""004 phase2b batch2 tariff scope expansion

Add Phase 2B Batch 2 tariff scope registry and broader tariff schedule metadata.

Revision ID: 004_phase2b_batch2_tariff_scope_expansion
Revises: 003_phase2b_batch1a_canonical_sku_data_layer
Create Date: 2026-04-15 08:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_phase2b_batch2_tariff_scope_expansion"
down_revision = "003_phase2b_batch1a_canonical_sku_data_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tariff_scope_registry",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("import_country", sa.String(length=3), nullable=False),
        sa.Column("coverage_level", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("update_cadence", sa.String(length=40), nullable=True),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_notes", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("import_country", name="uq_tariff_scope_registry_import_country"),
        schema="market",
    )
    op.create_index(
        "ix_tariff_scope_registry_import_country",
        "tariff_scope_registry",
        ["import_country"],
        schema="market",
    )
    op.create_index(
        "ix_tariff_scope_registry_coverage_level",
        "tariff_scope_registry",
        ["coverage_level"],
        schema="market",
    )

    op.add_column("tariff_schedules", sa.Column("hs6", sa.String(length=6), nullable=True), schema="market")
    op.add_column("tariff_schedules", sa.Column("hs_version", sa.String(length=20), nullable=True), schema="market")
    op.add_column("tariff_schedules", sa.Column("national_extension_code", sa.String(length=20), nullable=True), schema="market")
    op.add_column(
        "tariff_schedules",
        sa.Column("tariff_code_type", sa.String(length=20), nullable=False, server_default="HS6"),
        schema="market",
    )
    op.add_column("tariff_schedules", sa.Column("import_country", sa.String(length=3), nullable=True), schema="market")
    op.add_column("tariff_schedules", sa.Column("coverage_level", sa.String(length=40), nullable=True), schema="market")
    op.add_column("tariff_schedules", sa.Column("source_record_id", sa.String(length=160), nullable=True), schema="market")
    op.add_column("tariff_schedules", sa.Column("source_record_hash", sa.String(length=128), nullable=True), schema="market")
    op.add_column(
        "tariff_schedules",
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        schema="market",
    )
    op.add_column(
        "tariff_schedules",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="market",
    )

    op.execute("UPDATE market.tariff_schedules SET hs6 = substring(regexp_replace(hs_code, '[^0-9]', '', 'g') from 1 for 6)")
    op.execute("UPDATE market.tariff_schedules SET import_country = destination_country WHERE import_country IS NULL")
    op.execute("UPDATE market.tariff_schedules SET coverage_level = 'legacy' WHERE coverage_level IS NULL")

    op.create_index(
        "ix_tariff_hs6_destination",
        "tariff_schedules",
        ["hs6", "destination_country"],
        schema="market",
    )
    op.create_index(
        "ix_tariff_effective_window",
        "tariff_schedules",
        ["effective_from", "effective_to"],
        schema="market",
    )
    op.create_unique_constraint(
        "uq_tariff_schedule_version_hash",
        "tariff_schedules",
        ["destination_country", "origin_country", "hs_code", "effective_from", "source_record_hash"],
        schema="market",
    )


def downgrade() -> None:
    op.drop_constraint("uq_tariff_schedule_version_hash", "tariff_schedules", schema="market", type_="unique")
    op.drop_index("ix_tariff_effective_window", table_name="tariff_schedules", schema="market")
    op.drop_index("ix_tariff_hs6_destination", table_name="tariff_schedules", schema="market")

    op.drop_column("tariff_schedules", "updated_at", schema="market")
    op.drop_column("tariff_schedules", "source_metadata", schema="market")
    op.drop_column("tariff_schedules", "source_record_hash", schema="market")
    op.drop_column("tariff_schedules", "source_record_id", schema="market")
    op.drop_column("tariff_schedules", "coverage_level", schema="market")
    op.drop_column("tariff_schedules", "import_country", schema="market")
    op.drop_column("tariff_schedules", "tariff_code_type", schema="market")
    op.drop_column("tariff_schedules", "national_extension_code", schema="market")
    op.drop_column("tariff_schedules", "hs_version", schema="market")
    op.drop_column("tariff_schedules", "hs6", schema="market")

    op.drop_index("ix_tariff_scope_registry_coverage_level", table_name="tariff_scope_registry", schema="market")
    op.drop_index("ix_tariff_scope_registry_import_country", table_name="tariff_scope_registry", schema="market")
    op.drop_table("tariff_scope_registry", schema="market")
