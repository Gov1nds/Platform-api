"""002 phase2a data layer

Add Phase 2A Batch 1 data-layer tables only.

Revision ID: 002_phase2a_data_layer
Revises: 0001_baseline
Create Date: 2026-04-14 08:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002_phase2a_data_layer"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Schema: pricing ──────────────────────────────────────────────────

    op.create_table(
        "part_to_sku_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("mpn", sa.Text(), nullable=True),
        sa.Column("normalized_mpn", sa.Text(), nullable=True),
        sa.Column("vendor_sku", sa.Text(), nullable=False),
        sa.Column("sku_kind", sa.String(40), nullable=False, server_default="catalog"),
        sa.Column("match_method", sa.String(40), nullable=False, server_default="exact"),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("vendor_id", "vendor_sku", name="uq_ptsm_vendor_sku"),
        sa.UniqueConstraint("source_system", "source_record_id", name="uq_ptsm_source_record"),
        sa.UniqueConstraint("source_record_hash", name="uq_ptsm_source_hash"),
        schema="pricing",
    )
    op.create_index("ix_ptsm_bom_part_id", "part_to_sku_mapping", ["bom_part_id"], schema="pricing")
    op.create_index("ix_ptsm_vendor_id", "part_to_sku_mapping", ["vendor_id"], schema="pricing")
    op.create_index("ix_ptsm_canonical_part_key", "part_to_sku_mapping", ["canonical_part_key"], schema="pricing")
    op.create_index("ix_ptsm_mpn_lookup", "part_to_sku_mapping", ["manufacturer", "normalized_mpn"], schema="pricing")

    op.create_table(
        "sku_offers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("part_to_sku_mapping_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.part_to_sku_mapping.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("offer_name", sa.Text(), nullable=True),
        sa.Column("offer_status", sa.String(40), nullable=False, server_default="ACTIVE"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("uom", sa.String(40), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("spq", sa.Numeric(20, 8), nullable=True),
        sa.Column("lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("packaging", sa.Text(), nullable=True),
        sa.Column("incoterm", sa.String(20), nullable=True),
        sa.Column("country_of_origin", sa.String(3), nullable=True),
        sa.Column("factory_region", sa.Text(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_system", "source_record_id", name="uq_sku_offer_source_record"),
        sa.UniqueConstraint("source_record_hash", name="uq_sku_offer_source_hash"),
        schema="pricing",
    )
    op.create_index("ix_sku_offers_mapping_id", "sku_offers", ["part_to_sku_mapping_id"], schema="pricing")
    op.create_index("ix_sku_offers_vendor_id", "sku_offers", ["vendor_id"], schema="pricing")
    op.create_index("ix_sku_offers_status", "sku_offers", ["offer_status"], schema="pricing")
    op.create_index("ix_sku_offers_validity", "sku_offers", ["valid_from", "valid_to"], schema="pricing")

    op.create_table(
        "sku_offer_price_breaks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("sku_offer_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.sku_offers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("break_qty", sa.Numeric(20, 8), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("extended_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("price_type", sa.String(40), nullable=False, server_default="unit"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("sku_offer_id", "break_qty", "currency", "valid_from", name="uq_sku_offer_break_version"),
        sa.UniqueConstraint("source_record_hash", name="uq_sku_offer_break_source_hash"),
        schema="pricing",
    )
    op.create_index("ix_sku_offer_breaks_offer_id", "sku_offer_price_breaks", ["sku_offer_id"], schema="pricing")
    op.create_index("ix_sku_offer_breaks_qty", "sku_offer_price_breaks", ["break_qty"], schema="pricing")

    # ── Schema: market ───────────────────────────────────────────────────

    op.create_table(
        "sku_availability_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("sku_offer_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.sku_offers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("availability_status", sa.String(40), nullable=False, server_default="UNKNOWN"),
        sa.Column("available_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("on_order_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("allocated_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("backorder_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("factory_lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("inventory_location", sa.Text(), nullable=True),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("sku_offer_id", "inventory_location", "snapshot_at", name="uq_sku_availability_point"),
        sa.UniqueConstraint("source_record_hash", name="uq_sku_availability_source_hash"),
        schema="market",
    )
    op.create_index("ix_sku_availability_offer_id", "sku_availability_snapshots", ["sku_offer_id"], schema="market")
    op.create_index("ix_sku_availability_snapshot_at", "sku_availability_snapshots", ["snapshot_at"], schema="market")
    op.create_index("ix_sku_availability_status", "sku_availability_snapshots", ["availability_status"], schema="market")

    op.create_table(
        "hs_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("category_code", sa.Text(), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("hs_code", sa.String(32), nullable=False),
        sa.Column("hs_version", sa.String(20), nullable=True),
        sa.Column("jurisdiction", sa.String(3), nullable=True),
        sa.Column("mapping_method", sa.String(40), nullable=False, server_default="rule_based"),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("review_status", sa.String(40), nullable=False, server_default="AUTO"),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_system", "source_record_id", name="uq_hs_mapping_source_record"),
        sa.UniqueConstraint("source_record_hash", name="uq_hs_mapping_source_hash"),
        schema="market",
    )
    op.create_index("ix_hs_mapping_bom_part_id", "hs_mapping", ["bom_part_id"], schema="market")
    op.create_index("ix_hs_mapping_canonical_part_key", "hs_mapping", ["canonical_part_key"], schema="market")
    op.create_index("ix_hs_mapping_hs_code", "hs_mapping", ["hs_code"], schema="market")

    op.create_table(
        "lane_rate_bands",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("origin_country", sa.String(3), nullable=False),
        sa.Column("origin_region", sa.Text(), nullable=True),
        sa.Column("destination_country", sa.String(3), nullable=False),
        sa.Column("destination_region", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="sea"),
        sa.Column("min_weight_kg", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_weight_kg", sa.Numeric(20, 8), nullable=True),
        sa.Column("min_volume_cbm", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_volume_cbm", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("rate_type", sa.String(40), nullable=False, server_default="per_kg"),
        sa.Column("rate_value", sa.Numeric(20, 8), nullable=False),
        sa.Column("min_charge", sa.Numeric(20, 8), nullable=True),
        sa.Column("transit_days_min", sa.Integer(), nullable=True),
        sa.Column("transit_days_max", sa.Integer(), nullable=True),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "origin_country",
            "destination_country",
            "mode",
            "min_weight_kg",
            "max_weight_kg",
            "effective_from",
            "source_system",
            name="uq_lane_rate_band_version",
        ),
        sa.UniqueConstraint("source_record_hash", name="uq_lane_rate_band_source_hash"),
        schema="market",
    )
    op.create_index("ix_lane_rate_bands_route", "lane_rate_bands", ["origin_country", "destination_country", "mode"], schema="market")
    op.create_index("ix_lane_rate_bands_effective", "lane_rate_bands", ["effective_from", "effective_to"], schema="market")
    op.create_index("ix_lane_rate_bands_regions", "lane_rate_bands", ["origin_region", "destination_region"], schema="market")

    # ── Schema: bom ──────────────────────────────────────────────────────

    op.create_table(
        "bom_line_dependency_index",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("child_bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependency_type", sa.String(40), nullable=False, server_default="requires"),
        sa.Column("dependency_strength", sa.Numeric(12, 6), nullable=False, server_default="1"),
        sa.Column("sequence_no", sa.Integer(), nullable=True),
        sa.Column("dependency_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("source_record_id", sa.String(160), nullable=True),
        sa.Column("source_record_hash", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("bom_id", "parent_bom_part_id", "child_bom_part_id", "dependency_type", name="uq_bom_line_dependency_edge"),
        sa.UniqueConstraint("source_record_hash", name="uq_bom_line_dependency_source_hash"),
        schema="bom",
    )
    op.create_index("ix_bldi_bom_id", "bom_line_dependency_index", ["bom_id"], schema="bom")
    op.create_index("ix_bldi_parent", "bom_line_dependency_index", ["parent_bom_part_id"], schema="bom")
    op.create_index("ix_bldi_child", "bom_line_dependency_index", ["child_bom_part_id"], schema="bom")

    # ── Schema: ops ──────────────────────────────────────────────────────

    op.create_table(
        "enrichment_run_log",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_scope", sa.String(40), nullable=False, server_default="bom_line"),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="started"),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("freshness_status", sa.String(20), nullable=True),
        sa.Column("request_hash", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(80), nullable=False, server_default="platform-api"),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_enrichment_run_idempotency_key"),
        schema="ops",
    )
    op.create_index("ix_enrichment_run_bom_id", "enrichment_run_log", ["bom_id"], schema="ops")
    op.create_index("ix_enrichment_run_bom_part_id", "enrichment_run_log", ["bom_part_id"], schema="ops")
    op.create_index("ix_enrichment_run_project_id", "enrichment_run_log", ["project_id"], schema="ops")
    op.create_index("ix_enrichment_run_stage_status", "enrichment_run_log", ["stage", "status"], schema="ops")
    op.create_index("ix_enrichment_run_started_at", "enrichment_run_log", ["started_at"], schema="ops")


def downgrade() -> None:
    op.drop_index("ix_enrichment_run_started_at", table_name="enrichment_run_log", schema="ops")
    op.drop_index("ix_enrichment_run_stage_status", table_name="enrichment_run_log", schema="ops")
    op.drop_index("ix_enrichment_run_project_id", table_name="enrichment_run_log", schema="ops")
    op.drop_index("ix_enrichment_run_bom_part_id", table_name="enrichment_run_log", schema="ops")
    op.drop_index("ix_enrichment_run_bom_id", table_name="enrichment_run_log", schema="ops")
    op.drop_table("enrichment_run_log", schema="ops")

    op.drop_index("ix_bldi_child", table_name="bom_line_dependency_index", schema="bom")
    op.drop_index("ix_bldi_parent", table_name="bom_line_dependency_index", schema="bom")
    op.drop_index("ix_bldi_bom_id", table_name="bom_line_dependency_index", schema="bom")
    op.drop_table("bom_line_dependency_index", schema="bom")

    op.drop_index("ix_lane_rate_bands_regions", table_name="lane_rate_bands", schema="market")
    op.drop_index("ix_lane_rate_bands_effective", table_name="lane_rate_bands", schema="market")
    op.drop_index("ix_lane_rate_bands_route", table_name="lane_rate_bands", schema="market")
    op.drop_table("lane_rate_bands", schema="market")

    op.drop_index("ix_hs_mapping_hs_code", table_name="hs_mapping", schema="market")
    op.drop_index("ix_hs_mapping_canonical_part_key", table_name="hs_mapping", schema="market")
    op.drop_index("ix_hs_mapping_bom_part_id", table_name="hs_mapping", schema="market")
    op.drop_table("hs_mapping", schema="market")

    op.drop_index("ix_sku_availability_status", table_name="sku_availability_snapshots", schema="market")
    op.drop_index("ix_sku_availability_snapshot_at", table_name="sku_availability_snapshots", schema="market")
    op.drop_index("ix_sku_availability_offer_id", table_name="sku_availability_snapshots", schema="market")
    op.drop_table("sku_availability_snapshots", schema="market")

    op.drop_index("ix_sku_offer_breaks_qty", table_name="sku_offer_price_breaks", schema="pricing")
    op.drop_index("ix_sku_offer_breaks_offer_id", table_name="sku_offer_price_breaks", schema="pricing")
    op.drop_table("sku_offer_price_breaks", schema="pricing")

    op.drop_index("ix_sku_offers_validity", table_name="sku_offers", schema="pricing")
    op.drop_index("ix_sku_offers_status", table_name="sku_offers", schema="pricing")
    op.drop_index("ix_sku_offers_vendor_id", table_name="sku_offers", schema="pricing")
    op.drop_index("ix_sku_offers_mapping_id", table_name="sku_offers", schema="pricing")
    op.drop_table("sku_offers", schema="pricing")

    op.drop_index("ix_ptsm_mpn_lookup", table_name="part_to_sku_mapping", schema="pricing")
    op.drop_index("ix_ptsm_canonical_part_key", table_name="part_to_sku_mapping", schema="pricing")
    op.drop_index("ix_ptsm_vendor_id", table_name="part_to_sku_mapping", schema="pricing")
    op.drop_index("ix_ptsm_bom_part_id", table_name="part_to_sku_mapping", schema="pricing")
    op.drop_table("part_to_sku_mapping", schema="pricing")