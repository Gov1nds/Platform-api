"""012 phase3 vendor intelligence model

Revision ID: 012_phase3_vendor_intelligence_model
Revises: 011_phase2c_batch2c4_confidence_calibration_and_stability
Create Date: 2026-04-16 00:00:00.000000

Adds Phase 3 vendor intelligence tables:
  - pricing.vendor_locations
  - pricing.vendor_export_capabilities
  - pricing.vendor_lead_time_bands
  - pricing.vendor_communication_scores
  - pricing.vendor_trust_tiers

Extends pricing.vendors with intelligence columns:
  trade_name, founded_year, employee_count_band, export_capable,
  communication_score, trust_tier, missing_required_fields, profile_flags,
  primary_category_tag, secondary_category_tags, validation_errors,
  last_validated_at, address_validated, dedup_fingerprint,
  merged_into_vendor_id.

Pure additive migration. Does not alter or drop any pre-existing column.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012_phase3_vendor_intelligence_model"
down_revision = "011_phase2c_batch2c4_confidence_calibration_and_stability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── vendor_locations ───────────────────────────────────────────────────
    op.create_table(
        "vendor_locations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_line2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state_province", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("country_iso2", sa.String(length=2), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 6), nullable=True),
        sa.Column("geo_region_tag", sa.String(length=80), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_export_office", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vendor_locations_vendor", "vendor_locations", ["vendor_id"], schema="pricing")
    op.create_index(
        "ix_vendor_locations_country_state",
        "vendor_locations",
        ["country_iso2", "state_province"],
        schema="pricing",
    )

    # ── vendor_export_capabilities ─────────────────────────────────────────
    op.create_table(
        "vendor_export_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hs_code", sa.String(length=20), nullable=True),
        sa.Column("hs_description", sa.Text(), nullable=True),
        sa.Column("export_country_iso2", sa.String(length=2), nullable=True),
        sa.Column("supported_incoterms", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("export_license_number", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vendor_export_cap_vendor", "vendor_export_capabilities", ["vendor_id"], schema="pricing")
    op.create_index("ix_vendor_export_cap_hs", "vendor_export_capabilities", ["hs_code"], schema="pricing")

    # ── vendor_lead_time_bands ─────────────────────────────────────────────
    op.create_table(
        "vendor_lead_time_bands",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_tag", sa.Text(), nullable=True),
        sa.Column("material_family", sa.Text(), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("moq_unit", sa.String(length=30), nullable=True),
        sa.Column("lead_time_min_days", sa.Integer(), nullable=True),
        sa.Column("lead_time_max_days", sa.Integer(), nullable=True),
        sa.Column("lead_time_typical_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0.5"),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="self_reported"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vendor_lead_time_bands_vendor", "vendor_lead_time_bands", ["vendor_id"], schema="pricing")

    # ── vendor_communication_scores ────────────────────────────────────────
    op.create_table(
        "vendor_communication_scores",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("avg_response_time_hours", sa.Numeric(12, 4), nullable=True),
        sa.Column("rfq_response_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("communication_quality_score", sa.Numeric(6, 4), nullable=True),
        sa.Column("total_rfqs_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_rfqs_responded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("vendor_id", name="uq_vendor_communication_scores_vendor"),
        schema="pricing",
    )

    # ── vendor_trust_tiers ─────────────────────────────────────────────────
    op.create_table(
        "vendor_trust_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tier", sa.String(length=20), nullable=False, server_default="UNVERIFIED"),
        sa.Column("data_completeness_score", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("reliability_score", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_required_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("vendor_id", name="uq_vendor_trust_tiers_vendor"),
        schema="pricing",
    )
    op.create_index("ix_vendor_trust_tiers_vendor", "vendor_trust_tiers", ["vendor_id"], schema="pricing")

    # ── pricing.vendors additive columns ───────────────────────────────────
    with op.batch_alter_table("vendors", schema="pricing") as batch:
        batch.add_column(sa.Column("trade_name", sa.Text(), nullable=True))
        batch.add_column(sa.Column("founded_year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("employee_count_band", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("export_capable", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("communication_score", sa.Numeric(6, 4), nullable=True))
        batch.add_column(sa.Column("trust_tier", sa.String(length=20), nullable=False, server_default="UNVERIFIED"))
        batch.add_column(sa.Column("missing_required_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
        batch.add_column(sa.Column("profile_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
        batch.add_column(sa.Column("primary_category_tag", sa.Text(), nullable=True))
        batch.add_column(sa.Column("secondary_category_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
        batch.add_column(sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
        batch.add_column(sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("address_validated", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("dedup_fingerprint", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("merged_into_vendor_id", postgresql.UUID(as_uuid=False), nullable=True))

    op.create_index("ix_vendors_trust_tier", "vendors", ["trust_tier"], schema="pricing")
    op.create_index("ix_vendors_dedup_fingerprint", "vendors", ["dedup_fingerprint"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_vendors_dedup_fingerprint", table_name="vendors", schema="pricing")
    op.drop_index("ix_vendors_trust_tier", table_name="vendors", schema="pricing")

    with op.batch_alter_table("vendors", schema="pricing") as batch:
        batch.drop_column("merged_into_vendor_id")
        batch.drop_column("dedup_fingerprint")
        batch.drop_column("address_validated")
        batch.drop_column("last_validated_at")
        batch.drop_column("validation_errors")
        batch.drop_column("secondary_category_tags")
        batch.drop_column("primary_category_tag")
        batch.drop_column("profile_flags")
        batch.drop_column("missing_required_fields")
        batch.drop_column("trust_tier")
        batch.drop_column("communication_score")
        batch.drop_column("export_capable")
        batch.drop_column("employee_count_band")
        batch.drop_column("founded_year")
        batch.drop_column("trade_name")

    op.drop_index("ix_vendor_trust_tiers_vendor", table_name="vendor_trust_tiers", schema="pricing")
    op.drop_table("vendor_trust_tiers", schema="pricing")
    op.drop_table("vendor_communication_scores", schema="pricing")
    op.drop_index("ix_vendor_lead_time_bands_vendor", table_name="vendor_lead_time_bands", schema="pricing")
    op.drop_table("vendor_lead_time_bands", schema="pricing")
    op.drop_index("ix_vendor_export_cap_hs", table_name="vendor_export_capabilities", schema="pricing")
    op.drop_index("ix_vendor_export_cap_vendor", table_name="vendor_export_capabilities", schema="pricing")
    op.drop_table("vendor_export_capabilities", schema="pricing")
    op.drop_index("ix_vendor_locations_country_state", table_name="vendor_locations", schema="pricing")
    op.drop_index("ix_vendor_locations_vendor", table_name="vendor_locations", schema="pricing")
    op.drop_table("vendor_locations", schema="pricing")
