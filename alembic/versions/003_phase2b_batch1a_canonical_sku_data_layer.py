"""003 phase2b batch1a canonical sku data layer

Add Phase 2B Batch 1A canonical SKU and evidence consolidation tables only.

Revision ID: 003_phase2b_batch1a_canonical_sku_data_layer
Revises: 002_phase2a_data_layer
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003_phase2b_batch1a_canonical_sku_data_layer"
down_revision = "002_phase2a_data_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Schema: pricing ──────────────────────────────────────────────────

    op.create_table(
        "canonical_sku",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("canonical_key", sa.String(length=160), nullable=False),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("mpn", sa.Text(), nullable=True),
        sa.Column("normalized_mpn", sa.Text(), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=True),
        sa.Column("sku_kind", sa.String(length=40), nullable=False, server_default="canonical"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ACTIVE"),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("consolidation_method", sa.String(length=40), nullable=False, server_default="rule_based"),
        sa.Column("review_status", sa.String(length=40), nullable=False, server_default="AUTO"),
        sa.Column("primary_vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_key", name="uq_canonical_sku_key"),
        sa.UniqueConstraint("canonical_part_key", "manufacturer", "normalized_mpn", name="uq_canonical_sku_part_mpn"),
        schema="pricing",
    )
    op.create_index("ix_canonical_sku_part_key", "canonical_sku", ["canonical_part_key"], schema="pricing")
    op.create_index("ix_canonical_sku_mpn_lookup", "canonical_sku", ["manufacturer", "normalized_mpn"], schema="pricing")
    op.create_index("ix_canonical_sku_status", "canonical_sku", ["status"], schema="pricing")

    op.create_table(
        "source_sku_link",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("canonical_sku_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_to_sku_mapping_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.part_to_sku_mapping.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_system", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("external_sku_key", sa.String(length=200), nullable=False),
        sa.Column("vendor_sku", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("mpn", sa.Text(), nullable=True),
        sa.Column("normalized_mpn", sa.Text(), nullable=True),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("link_role", sa.String(length=40), nullable=False, server_default="source"),
        sa.Column("link_status", sa.String(length=40), nullable=False, server_default="ACTIVE"),
        sa.Column("match_method", sa.String(length=40), nullable=False, server_default="exact"),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_sku_id", "part_to_sku_mapping_id", name="uq_source_sku_link_mapping"),
        sa.UniqueConstraint("canonical_sku_id", "source_system", "external_sku_key", name="uq_source_sku_link_external_key"),
        schema="pricing",
    )
    op.create_index("ix_source_sku_link_canonical_sku_id", "source_sku_link", ["canonical_sku_id"], schema="pricing")
    op.create_index("ix_source_sku_link_mapping_id", "source_sku_link", ["part_to_sku_mapping_id"], schema="pricing")
    op.create_index("ix_source_sku_link_vendor_id", "source_sku_link", ["vendor_id"], schema="pricing")
    op.create_index("ix_source_sku_link_status", "source_sku_link", ["link_status"], schema="pricing")

    op.create_table(
        "canonical_offer_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("canonical_sku_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sku_link_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.source_sku_link.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_offer_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.sku_offers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("offer_status", sa.String(length=40), nullable=False, server_default="ACTIVE"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("unit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("spq", sa.Numeric(20, 8), nullable=True),
        sa.Column("lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("packaging", sa.Text(), nullable=True),
        sa.Column("incoterm", sa.String(length=20), nullable=True),
        sa.Column("country_of_origin", sa.String(length=3), nullable=True),
        sa.Column("factory_region", sa.Text(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("consolidation_method", sa.String(length=40), nullable=False, server_default="phase2a_evidence"),
        sa.Column("freshness_status", sa.String(length=20), nullable=False, server_default="FRESH"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_sku_id", "source_sku_link_id", "source_offer_id", "observed_at", name="uq_canonical_offer_snapshot_point"),
        schema="pricing",
    )
    op.create_index("ix_canonical_offer_snapshot_canonical_sku_id", "canonical_offer_snapshot", ["canonical_sku_id"], schema="pricing")
    op.create_index("ix_canonical_offer_snapshot_source_offer_id", "canonical_offer_snapshot", ["source_offer_id"], schema="pricing")
    op.create_index("ix_canonical_offer_snapshot_vendor_id", "canonical_offer_snapshot", ["vendor_id"], schema="pricing")
    op.create_index("ix_canonical_offer_snapshot_observed_at", "canonical_offer_snapshot", ["observed_at"], schema="pricing")
    op.create_index("ix_canonical_offer_snapshot_freshness", "canonical_offer_snapshot", ["freshness_status"], schema="pricing")

    # ── Schema: market ───────────────────────────────────────────────────

    op.create_table(
        "canonical_availability_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("canonical_sku_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sku_link_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.source_sku_link.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_offer_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.sku_offers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_availability_snapshot_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("market.sku_availability_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("availability_status", sa.String(length=40), nullable=False, server_default="UNKNOWN"),
        sa.Column("available_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("on_order_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("allocated_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("backorder_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("factory_lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("inventory_location", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("consolidation_method", sa.String(length=40), nullable=False, server_default="phase2a_evidence"),
        sa.Column("freshness_status", sa.String(length=20), nullable=False, server_default="FRESH"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("evidence_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_sku_id", "inventory_location", "snapshot_at", "source_availability_snapshot_id", name="uq_canonical_availability_snapshot_point"),
        schema="market",
    )
    op.create_index("ix_canonical_availability_canonical_sku_id", "canonical_availability_snapshot", ["canonical_sku_id"], schema="market")
    op.create_index("ix_canonical_availability_source_offer_id", "canonical_availability_snapshot", ["source_offer_id"], schema="market")
    op.create_index("ix_canonical_availability_source_snapshot_id", "canonical_availability_snapshot", ["source_availability_snapshot_id"], schema="market")
    op.create_index("ix_canonical_availability_snapshot_at", "canonical_availability_snapshot", ["snapshot_at"], schema="market")
    op.create_index("ix_canonical_availability_status", "canonical_availability_snapshot", ["availability_status"], schema="market")

    # ── Schema: ops ──────────────────────────────────────────────────────

    op.create_table(
        "connector_health_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("connector_name", sa.String(length=120), nullable=False),
        sa.Column("metric_scope", sa.String(length=80), nullable=False, server_default="global"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="UNKNOWN"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("throttle_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
        sa.Column("freshness_lag_seconds", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_name", "metric_scope", "window_started_at", "window_ended_at", name="uq_connector_health_window"),
        schema="ops",
    )
    op.create_index("ix_connector_health_name", "connector_health_metrics", ["connector_name"], schema="ops")
    op.create_index("ix_connector_health_status", "connector_health_metrics", ["status"], schema="ops")
    op.create_index("ix_connector_health_window", "connector_health_metrics", ["window_started_at", "window_ended_at"], schema="ops")


def downgrade() -> None:
    op.drop_index("ix_connector_health_window", table_name="connector_health_metrics", schema="ops")
    op.drop_index("ix_connector_health_status", table_name="connector_health_metrics", schema="ops")
    op.drop_index("ix_connector_health_name", table_name="connector_health_metrics", schema="ops")
    op.drop_table("connector_health_metrics", schema="ops")

    op.drop_index("ix_canonical_availability_status", table_name="canonical_availability_snapshot", schema="market")
    op.drop_index("ix_canonical_availability_snapshot_at", table_name="canonical_availability_snapshot", schema="market")
    op.drop_index("ix_canonical_availability_source_snapshot_id", table_name="canonical_availability_snapshot", schema="market")
    op.drop_index("ix_canonical_availability_source_offer_id", table_name="canonical_availability_snapshot", schema="market")
    op.drop_index("ix_canonical_availability_canonical_sku_id", table_name="canonical_availability_snapshot", schema="market")
    op.drop_table("canonical_availability_snapshot", schema="market")

    op.drop_index("ix_canonical_offer_snapshot_freshness", table_name="canonical_offer_snapshot", schema="pricing")
    op.drop_index("ix_canonical_offer_snapshot_observed_at", table_name="canonical_offer_snapshot", schema="pricing")
    op.drop_index("ix_canonical_offer_snapshot_vendor_id", table_name="canonical_offer_snapshot", schema="pricing")
    op.drop_index("ix_canonical_offer_snapshot_source_offer_id", table_name="canonical_offer_snapshot", schema="pricing")
    op.drop_index("ix_canonical_offer_snapshot_canonical_sku_id", table_name="canonical_offer_snapshot", schema="pricing")
    op.drop_table("canonical_offer_snapshot", schema="pricing")

    op.drop_index("ix_source_sku_link_status", table_name="source_sku_link", schema="pricing")
    op.drop_index("ix_source_sku_link_vendor_id", table_name="source_sku_link", schema="pricing")
    op.drop_index("ix_source_sku_link_mapping_id", table_name="source_sku_link", schema="pricing")
    op.drop_index("ix_source_sku_link_canonical_sku_id", table_name="source_sku_link", schema="pricing")
    op.drop_table("source_sku_link", schema="pricing")

    op.drop_index("ix_canonical_sku_status", table_name="canonical_sku", schema="pricing")
    op.drop_index("ix_canonical_sku_mpn_lookup", table_name="canonical_sku", schema="pricing")
    op.drop_index("ix_canonical_sku_part_key", table_name="canonical_sku", schema="pricing")
    op.drop_table("canonical_sku", schema="pricing")