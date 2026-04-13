"""001 baseline

Baseline migration capturing the full canonical schema.
All 9 schemas, all tables, all indexes, all constraints.

Revision ID: 001_baseline
Revises:
Create Date: 2025-01-01 00:00:00.000000

References: GAP-025, INFERRED-004, platform-api-implementation.md Batch 2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Schema: auth ─────────────────────────────────────────────────────

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("type", sa.String(40), nullable=False, server_default="buyer"),
        sa.Column("settings_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="auth",
    )
    op.create_index("ix_org_slug", "organizations", ["slug"], unique=True, schema="auth")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(40), nullable=False, server_default="BUYER_EDITOR"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("oauth_provider", sa.String(40), nullable=True),
        sa.Column("oauth_provider_id", sa.String(320), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("mfa_secret", sa.Text(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="auth",
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True, schema="auth")
    op.create_index("ix_users_org", "users", ["organization_id"], schema="auth")

    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False, server_default="BUYER_VIEWER"),
        sa.Column("invited_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_orgmem_org_user"),
        schema="auth",
    )
    op.create_index("ix_orgmem_org", "organization_memberships", ["organization_id"], schema="auth")
    op.create_index("ix_orgmem_user", "organization_memberships", ["user_id"], schema="auth")

    op.create_table(
        "guest_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("session_token", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="ACTIVE"),
        sa.Column("merged_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_location", sa.Text(), nullable=True),
        sa.Column("detected_currency", sa.String(3), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("component_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="auth",
    )
    op.create_index("ix_guest_sessions_token", "guest_sessions", ["session_token"], unique=True, schema="auth")
    op.create_index("ix_guest_status_expires", "guest_sessions", ["status", "expires_at"], schema="auth")

    op.create_table(
        "vendor_users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), nullable=False, index=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(40), nullable=False, server_default="VENDOR_REP"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="auth",
    )
    op.create_index("ix_vendor_users_email", "vendor_users", ["email"], unique=True, schema="auth")

    # ── Schema: bom ──────────────────────────────────────────────────────

    op.create_table(
        "boms",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_file_name", sa.Text(), nullable=False, server_default="upload.csv"),
        sa.Column("source_file_type", sa.Text(), nullable=False, server_default="csv"),
        sa.Column("source_checksum", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(128), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("target_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("delivery_location", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False, server_default="balanced"),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("scan_status", sa.String(40), nullable=True),
        sa.Column("column_mapping_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("total_parts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="bom",
    )
    op.create_index("ix_boms_user_id", "boms", ["uploaded_by_user_id"], schema="bom")
    op.create_index("ix_boms_guest_session", "boms", ["guest_session_id"], schema="bom")
    op.create_index("ix_boms_project_id", "boms", ["project_id"], schema="bom")
    op.create_index("ix_boms_org", "boms", ["organization_id"], schema="bom")
    op.create_index("ix_boms_file_hash", "boms", ["file_hash"], schema="bom")

    op.create_table(
        "bom_parts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="RAW"),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(40), nullable=False, server_default="file"),
        sa.Column("item_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False, server_default="1"),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("part_number", sa.Text(), nullable=True),
        sa.Column("mpn", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.Text(), nullable=True),
        sa.Column("category_code", sa.Text(), nullable=True),
        sa.Column("procurement_class", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("material_form", sa.Text(), nullable=True),
        sa.Column("geometry", sa.Text(), nullable=True),
        sa.Column("tolerance", sa.Text(), nullable=True),
        sa.Column("secondary_ops", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("specs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("classification_confidence", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("classification_reason", sa.Text(), nullable=True),
        sa.Column("has_mpn", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_raw", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rfq_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("drawing_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("canonical_part_key", sa.Text(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=True, server_default="auto"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("normalization_status", sa.String(40), nullable=True),
        sa.Column("normalization_trace_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enrichment_status", sa.String(40), nullable=True),
        sa.Column("enrichment_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("scoring_status", sa.String(40), nullable=True),
        sa.Column("score_cache_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("strategy_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("risk_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("data_freshness_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="bom",
    )
    op.create_index("ix_bom_parts_bom_id", "bom_parts", ["bom_id"], schema="bom")
    op.create_index("ix_bom_parts_canonical_key", "bom_parts", ["canonical_part_key"], schema="bom")
    op.create_index("ix_bom_parts_status", "bom_parts", ["bom_id", "status"], schema="bom")
    op.create_index("ix_bom_parts_org", "bom_parts", ["organization_id"], schema="bom")

    op.create_table(
        "analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("algorithm_version", sa.String(40), nullable=True),
        sa.Column("report_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("strategy_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("scoring_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="bom",
    )
    op.create_index("ix_analysis_bom", "analysis_results", ["bom_id"], schema="bom")

    # ── Schema: projects ─────────────────────────────────────────────────

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sourcing_case_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("name", sa.Text(), nullable=False, server_default="Uploaded BOM"),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="DRAFT"),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="owner_only"),
        sa.Column("weight_profile", sa.String(40), nullable=False, server_default="balanced"),
        sa.Column("total_parts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bom_upload_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bom_line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rfq_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("po_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Numeric(20, 8), nullable=True),
        sa.Column("cost_range_low", sa.Numeric(20, 8), nullable=True),
        sa.Column("cost_range_high", sa.Numeric(20, 8), nullable=True),
        sa.Column("lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("decision_summary", sa.Text(), nullable=True),
        sa.Column("current_rfq_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("current_po_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("analyzer_report", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("strategy", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("project_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="projects",
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"], schema="projects")
    op.create_index("ix_projects_guest_session", "projects", ["guest_session_id"], schema="projects")
    op.create_index("ix_projects_org", "projects", ["organization_id"], schema="projects")

    op.create_table(
        "project_acl",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("principal_type", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="projects",
    )
    op.create_index("ix_pacl_project", "project_acl", ["project_id"], schema="projects")
    op.create_index("ix_pacl_principal", "project_acl", ["principal_type", "principal_id"], schema="projects")

    op.create_table(
        "project_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="projects",
    )

    op.create_table(
        "search_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("session_token", sa.String(120), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("query_type", sa.String(40), nullable=False, server_default="component"),
        sa.Column("input_type", sa.String(40), nullable=False, server_default="text"),
        sa.Column("delivery_location", sa.Text(), nullable=True),
        sa.Column("target_currency", sa.String(10), nullable=True, server_default="USD"),
        sa.Column("results_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("analysis_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("promoted_to", sa.String(40), nullable=True),
        sa.Column("promoted_to_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="projects",
    )
    op.create_index("ix_ss_user", "search_sessions", ["user_id"], schema="projects")
    op.create_index("ix_ss_guest", "search_sessions", ["guest_session_id"], schema="projects")
    op.create_index("ix_ss_org", "search_sessions", ["organization_id"], schema="projects")

    op.create_table(
        "sourcing_cases",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("session_token", sa.String(120), nullable=True),
        sa.Column("search_session_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("name", sa.Text(), nullable=False, server_default="Saved search"),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("analysis_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("vendor_shortlist", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("promoted_to_project_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="projects",
    )
    op.create_index("ix_sc_user", "sourcing_cases", ["user_id"], schema="projects")
    op.create_index("ix_sc_guest", "sourcing_cases", ["guest_session_id"], schema="projects")
    op.create_index("ix_sc_org", "sourcing_cases", ["organization_id"], schema="projects")

    # ── Schema: pricing ──────────────────────────────────────────────────

    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.Text(), nullable=True),
        sa.Column("reliability_score", sa.Numeric(12, 6), nullable=False, server_default="0.8"),
        sa.Column("avg_lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("default_moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("regions_served", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("certifications", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("capacity_profile", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("quality_rating", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(40), nullable=False, server_default="GHOST"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("profile_completeness", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("identity_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("commercial_terms_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("lead_time_profile_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("onboarding_method", sa.String(40), nullable=True),
        sa.Column("claimed_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="pricing",
    )
    op.create_index("ix_vendors_org", "vendors", ["organization_id"], schema="pricing")
    op.create_index("ix_vendors_status", "vendors", ["status"], schema="pricing")

    op.create_table(
        "vendor_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process", sa.Text(), nullable=False),
        sa.Column("material_family", sa.Text(), nullable=True),
        sa.Column("proficiency", sa.Numeric(6, 4), nullable=False, server_default="0.8"),
        sa.Column("typical_lead_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("certifications", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vendor_cap_vendor", "vendor_capabilities", ["vendor_id"], schema="pricing")
    op.create_index("ix_vendor_cap_process", "vendor_capabilities", ["process"], schema="pricing")

    op.create_table(
        "vendor_match_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("weight_profile", sa.String(40), nullable=True),
        sa.Column("filters_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("weights_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("total_vendors_considered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="pricing",
    )
    op.create_index("ix_vmr_project", "vendor_match_runs", ["project_id"], schema="pricing")
    op.create_index("ix_vmr_org", "vendor_match_runs", ["organization_id"], schema="pricing")

    op.create_table(
        "vendor_matches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("match_run_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendor_match_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("explanation_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("shortlist_status", sa.Text(), nullable=False, server_default="shortlisted"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("elimination_reasons", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("confidence_level", sa.String(20), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vm_run", "vendor_matches", ["match_run_id"], schema="pricing")
    op.create_index("ix_vm_vendor", "vendor_matches", ["vendor_id"], schema="pricing")

    op.create_table(
        "vendor_performance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_pos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("on_time_delivery_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("quality_pass_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("avg_response_time_hours", sa.Numeric(12, 4), nullable=True),
        sa.Column("quote_win_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("trailing_window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_vps_vendor_date", "vendor_performance_snapshots", ["vendor_id", "snapshot_date"], schema="pricing")

    # ── Schema: sourcing ─────────────────────────────────────────────────
    # RFQ, Quote, PO, Invitation, Approval tables

    op.create_table(
        "rfq_batches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("bom_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(40), nullable=False, server_default="DRAFT"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minimum_vendors", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("terms_snapshot_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("batch_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_rfq_project", "rfq_batches", ["project_id"], schema="sourcing")
    op.create_index("ix_rfq_org", "rfq_batches", ["organization_id"], schema="sourcing")

    op.create_table(
        "rfq_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("rfq_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("part_key", sa.Text(), nullable=True),
        sa.Column("requested_quantity", sa.Numeric(20, 8), nullable=False, server_default="1"),
        sa.Column("requested_material", sa.Text(), nullable=True),
        sa.Column("requested_process", sa.Text(), nullable=True),
        sa.Column("drawing_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("spec_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_rfq_items_rfq", "rfq_items", ["rfq_batch_id"], schema="sourcing")
    op.create_index("ix_rfq_items_bom_line", "rfq_items", ["bom_line_id"], schema="sourcing")

    op.create_table(
        "rfq_vendor_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("rfq_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("invited_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("portal_token", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_rvi_rfq", "rfq_vendor_invitations", ["rfq_batch_id"], schema="sourcing")
    op.create_index("ix_rvi_vendor", "rfq_vendor_invitations", ["vendor_id"], schema="sourcing")

    op.create_table(
        "invitation_status_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("invitation_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_vendor_invitations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="sourcing",
    )

    op.create_table(
        "rfq_quote_headers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("rfq_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("quote_number", sa.Text(), nullable=True),
        sa.Column("quote_status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("award_status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("quote_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("incoterms", sa.Text(), nullable=True),
        sa.Column("quote_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_revision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("parent_quote_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("subtotal", sa.Numeric(20, 8), nullable=True),
        sa.Column("freight", sa.Numeric(20, 8), nullable=True),
        sa.Column("taxes", sa.Numeric(20, 8), nullable=True),
        sa.Column("total", sa.Numeric(20, 8), nullable=True),
        sa.Column("forex_rate_at_submission", sa.Numeric(20, 8), nullable=True),
        sa.Column("forex_rate_currency_pair", sa.String(7), nullable=True),
        sa.Column("terms_hash", sa.String(128), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("response_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_rqh_rfq", "rfq_quote_headers", ["rfq_batch_id"], schema="sourcing")
    op.create_index("ix_rqh_vendor", "rfq_quote_headers", ["vendor_id"], schema="sourcing")
    op.create_index("ix_rqh_org", "rfq_quote_headers", ["organization_id"], schema="sourcing")

    op.create_table(
        "rfq_quote_lines",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("quote_header_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_quote_headers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rfq_item_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("part_name", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("line_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("lead_time_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("moq", sa.Numeric(20, 8), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("line_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_rql_header", "rfq_quote_lines", ["quote_header_id"], schema="sourcing")

    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rfq_batch_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.rfq_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("pricing.vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("po_number", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PO_APPROVED"),
        sa.Column("total", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("shipping_terms", sa.Text(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("terms_snapshot_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vendor_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_response_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("po_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_po_project", "purchase_orders", ["project_id"], schema="sourcing")
    op.create_index("ix_po_org", "purchase_orders", ["organization_id"], schema="sourcing")

    op.create_table(
        "po_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bom_part_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("quote_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("total_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_poli_po", "po_line_items", ["po_id"], schema="sourcing")

    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("threshold_amount", sa.Numeric(20, 8), nullable=True),
        sa.Column("decision", sa.String(40), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("escalated_to_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="sourcing",
    )
    op.create_index("ix_ar_entity", "approval_requests", ["entity_type", "entity_id"], schema="sourcing")
    op.create_index("ix_ar_org", "approval_requests", ["organization_id"], schema="sourcing")
    op.create_index("ix_ar_assigned", "approval_requests", ["assigned_to_user_id"], schema="sourcing")

    # ── Schema: finance ──────────────────────────────────────────────────

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.purchase_orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="RECEIVED"),
        sa.Column("amount", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("three_way_match_result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("dispute_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="finance",
    )
    op.create_index("ix_inv_po", "invoices", ["po_id"], schema="finance")
    op.create_index("ix_inv_org", "invoices", ["organization_id"], schema="finance")

    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("finance.invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("unit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("line_total", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="finance",
    )
    op.create_index("ix_invl_invoice", "invoice_lines", ["invoice_id"], schema="finance")

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("finance.invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("method", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(40), nullable=True),
        sa.Column("payment_reference", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("erp_sync_status", sa.String(40), nullable=True),
        sa.Column("erp_reference", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="finance",
    )
    op.create_index("ix_pay_invoice", "payments", ["invoice_id"], schema="finance")
    op.create_index("ix_pay_org", "payments", ["organization_id"], schema="finance")

    op.create_table(
        "goods_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("auth.organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("received_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="finance",
    )
    op.create_index("ix_gr_po", "goods_receipts", ["po_id"], schema="finance")
    op.create_index("ix_gr_org", "goods_receipts", ["organization_id"], schema="finance")

    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("goods_receipt_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("finance.goods_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("bom_line_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("expected_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("received_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("accepted_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("discrepancy_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="finance",
    )
    op.create_index("ix_grl_gr", "goods_receipt_lines", ["goods_receipt_id"], schema="finance")

    # ── Schema: logistics ────────────────────────────────────────────────

    op.create_table(
        "shipments",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("carrier", sa.Text(), nullable=True),
        sa.Column("tracking_number", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="BOOKED"),
        sa.Column("carrier_integration_id", sa.String(120), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_delivery", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_alert_sent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("shipment_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="logistics",
    )
    op.create_index("ix_shipments_po", "shipments", ["po_id"], schema="logistics")
    op.create_index("ix_shipments_org", "shipments", ["organization_id"], schema="logistics")

    op.create_table(
        "shipment_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("shipment_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("logistics.shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("milestone_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="completed"),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("carrier_event_id", sa.String(120), nullable=True),
        sa.Column("is_delay", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("attachment_url", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="logistics",
    )
    op.create_index("ix_sm_shipment", "shipment_milestones", ["shipment_id"], schema="logistics")

    # ── Schema: market ───────────────────────────────────────────────────

    op.create_table(
        "fx_rates",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="1.0"),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(80), nullable=True),
        sa.Column("data_source", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index("ix_fx_pair", "fx_rates", ["base_currency", "quote_currency"], schema="market")
    op.create_index("ix_fx_freshness", "fx_rates", ["freshness_status"], schema="market")

    op.create_table(
        "freight_rates",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("origin_region", sa.Text(), nullable=False),
        sa.Column("destination_region", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False, server_default="sea"),
        sa.Column("rate_per_kg", sa.Numeric(20, 8), nullable=True),
        sa.Column("rate_per_cbm", sa.Numeric(20, 8), nullable=True),
        sa.Column("min_charge", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("transit_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0.7"),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(80), nullable=True),
        sa.Column("data_source", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index("ix_freight_route", "freight_rates", ["origin_region", "destination_region"], schema="market")

    op.create_table(
        "tariff_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("hs_code", sa.String(20), nullable=False),
        sa.Column("origin_country", sa.String(3), nullable=False),
        sa.Column("destination_country", sa.String(3), nullable=False),
        sa.Column("duty_rate_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("additional_taxes_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0.6"),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False, server_default="604800"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(80), nullable=True),
        sa.Column("data_source", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index("ix_tariff_hs", "tariff_schedules", ["hs_code"], schema="market")

    op.create_table(
        "commodity_indices",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("commodity_name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="kg"),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False, server_default="0.7"),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(80), nullable=True),
        sa.Column("data_source", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index("ix_commodity_name", "commodity_indices", ["commodity_name"], schema="market")

    # ── Schema: ops ──────────────────────────────────────────────────────

    op.create_table(
        "platform_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="user"),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )

    op.create_table(
        "event_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("actor_type", sa.String(40), nullable=False, server_default="SYSTEM"),
        sa.Column("from_state", sa.String(40), nullable=True),
        sa.Column("to_state", sa.String(40), nullable=True),
        sa.Column("field_changes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(32), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )
    op.create_index("ix_eal_entity", "event_audit_log", ["entity_type", "entity_id"], schema="ops")
    op.create_index("ix_eal_actor", "event_audit_log", ["actor_id"], schema="ops")
    op.create_index("ix_eal_created", "event_audit_log", ["created_at"], schema="ops")
    op.create_index("ix_eal_type", "event_audit_log", ["event_type"], schema="ops")
    op.create_index("ix_eal_org", "event_audit_log", ["organization_id"], schema="ops")

    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("response_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="ops",
    )

    op.create_table(
        "report_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=True),
        sa.Column("scope_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=True),
        sa.Column("generated_by", sa.String(40), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filters_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("data_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )
    op.create_index("ix_rs_org", "report_snapshots", ["organization_id"], schema="ops")
    op.create_index("ix_rs_run", "report_snapshots", ["report_run_id"], schema="ops")

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False, server_default="in_app"),
        sa.Column("delivery_status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )
    op.create_index("ix_notif_user", "notifications", ["user_id"], schema="ops")
    op.create_index("ix_notif_read", "notifications", ["user_id", "read_at"], schema="ops")
    op.create_index("ix_notif_org", "notifications", ["organization_id"], schema="ops")

    op.create_table(
        "notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("notification_type", sa.String(80), nullable=False),
        sa.Column("channel_email", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("channel_sms", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("channel_push", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("channel_in_app", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="ops",
    )
    op.create_index("ix_notifpref_user", "notification_preferences", ["user_id"], schema="ops")

    op.create_table(
        "integration_run_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("integration_id", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_payload_hash", sa.String(128), nullable=True),
        sa.Column("response_record_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )
    op.create_index("ix_irl_integration", "integration_run_logs", ["integration_id"], schema="ops")
    op.create_index("ix_irl_provider", "integration_run_logs", ["provider"], schema="ops")
    op.create_index("ix_irl_created", "integration_run_logs", ["created_at"], schema="ops")

    # ── Schema: ops — chat ───────────────────────────────────────────────

    op.create_table(
        "chat_threads",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("context_type", sa.Text(), nullable=False),
        sa.Column("context_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="ops",
    )
    op.create_index("ix_ct_context", "chat_threads", ["context_type", "context_id"], schema="ops")
    op.create_index("ix_ct_org", "chat_threads", ["organization_id"], schema="ops")

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("ops.chat_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("sender_vendor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="internal"),
        sa.Column("message_type", sa.String(40), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attachment_url", sa.Text(), nullable=True),
        sa.Column("offer_event_json", postgresql.JSONB(), nullable=True),
        sa.Column("message_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="ops",
    )
    op.create_index("ix_cm_thread", "chat_messages", ["thread_id"], schema="ops")


def downgrade() -> None:
    # Drop in reverse dependency order
    for table, schema in [
        ("chat_messages", "ops"),
        ("chat_threads", "ops"),
        ("integration_run_logs", "ops"),
        ("notification_preferences", "ops"),
        ("notifications", "ops"),
        ("report_snapshots", "ops"),
        ("idempotency_records", "ops"),
        ("event_audit_log", "ops"),
        ("platform_events", "ops"),
        ("shipment_milestones", "logistics"),
        ("shipments", "logistics"),
        ("goods_receipt_lines", "finance"),
        ("goods_receipts", "finance"),
        ("payments", "finance"),
        ("invoice_lines", "finance"),
        ("invoices", "finance"),
        ("approval_requests", "sourcing"),
        ("po_line_items", "sourcing"),
        ("purchase_orders", "sourcing"),
        ("rfq_quote_lines", "sourcing"),
        ("rfq_quote_headers", "sourcing"),
        ("invitation_status_events", "sourcing"),
        ("rfq_vendor_invitations", "sourcing"),
        ("rfq_items", "sourcing"),
        ("rfq_batches", "sourcing"),
        ("vendor_performance_snapshots", "pricing"),
        ("vendor_matches", "pricing"),
        ("vendor_match_runs", "pricing"),
        ("vendor_capabilities", "pricing"),
        ("vendors", "pricing"),
        ("sourcing_cases", "projects"),
        ("search_sessions", "projects"),
        ("project_events", "projects"),
        ("project_acl", "projects"),
        ("projects", "projects"),
        ("analysis_results", "bom"),
        ("bom_parts", "bom"),
        ("boms", "bom"),
        ("vendor_users", "auth"),
        ("guest_sessions", "auth"),
        ("organization_memberships", "auth"),
        ("users", "auth"),
        ("organizations", "auth"),
    ]:
        op.drop_table(table, schema=schema)