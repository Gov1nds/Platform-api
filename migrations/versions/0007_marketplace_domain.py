"""0007 — Marketplace domain: vendor and related tables; finalize vendor_user FK.

Revision ID: 0007
Revises: 0006
Create Date: 2024-01-01

Contract anchors:
  §2.13 Vendor                  §2.15 Vendor_Part_Capability
  §2.16 Vendor_Performance_Snapshot  §2.17 Certification
  §2.18 Vendor_Profile_Claim    §2.19 Vendor_Invite
  §2.20 Vendor_Tier_Transition  §2.14 VendorUser (FK finalized)
  §3.10-§3.13, §3.39-§3.41

Notes:
  - vendor.profile_claimed_by → identity.user (ON DELETE SET NULL)
  - vendor_user.vendor_id FK added here (table created in 0002)
  - preferred_vendor_member.vendor_id FK added here
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MKT = "marketplace"
IDN = "identity"
INTEL = "intelligence"


def upgrade() -> None:

    # ── vendor (§2.13) ───────────────────────────────────────────────────────
    op.create_table(
        "vendor",
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_entity", sa.String(255), nullable=True),
        sa.Column("registration_number", sa.String(128), nullable=True),
        sa.Column(
            "vendor_type",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'manufacturer'"),
        ),
        sa.Column("country_of_origin", sa.String(2), nullable=False),
        sa.Column(
            "regions_served",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "commodity_groups",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # certifications: denormalized cache only; authoritative in certification table (§2.93)
        sa.Column(
            "certifications",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "capacity_profile",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "min_order_value",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "moq_by_category",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "lead_time_profile",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("quality_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("reliability_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("response_speed", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "payment_terms",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "shipping_terms",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tax_profile",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "currency_support",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "substitute_willingness",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "engineering_support",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "ships_on_their_own",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "active_status",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "profile_claimed_by",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="SET NULL",
                name="fk_vendor_profile_claimed_by_user",
            ),
            nullable=True,
        ),
        sa.Column(
            "profile_completion_pct",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "verified_badge",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "participation",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'GHOST'"),
        ),
        sa.Column(
            "tier",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'GHOST'"),
        ),
        sa.Column(
            "platform_integration_level",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'email_only'"),
        ),
        sa.Column("chat_response_avg_seconds", sa.Integer, nullable=True),
        sa.Column("last_active_on_platform", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("onboarded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "vendor_type IN ('manufacturer','distributor','contract_manufacturer','broker','trading_company')",
            name="ck_vendor_vendor_type",
        ),
        sa.CheckConstraint(
            "participation IN ('GHOST','INVITED','CLAIM_PENDING','BASIC','STANDARD',"
            "'PREMIUM','SUSPENDED','DEACTIVATED')",
            name="ck_vendor_participation",
        ),
        sa.CheckConstraint(
            "tier IN ('GHOST','BASIC','STANDARD','PREMIUM')",
            name="ck_vendor_tier",
        ),
        sa.CheckConstraint(
            "platform_integration_level IN ('api_connected','portal_only','email_only')",
            name="ck_vendor_platform_integration_level",
        ),
        sa.CheckConstraint("min_order_value >= 0", name="ck_vendor_min_order_value_nonneg"),
        sa.CheckConstraint(
            "quality_score BETWEEN 0 AND 100 OR quality_score IS NULL",
            name="ck_vendor_quality_score_range",
        ),
        sa.CheckConstraint(
            "reliability_score BETWEEN 0 AND 100 OR reliability_score IS NULL",
            name="ck_vendor_reliability_score_range",
        ),
        sa.CheckConstraint(
            "profile_completion_pct BETWEEN 0 AND 100",
            name="ck_vendor_profile_completion_pct_range",
        ),
        schema=MKT,
    )
    op.execute(
        "CREATE INDEX ix_vendor_commodity_groups_gin "
        "ON marketplace.vendor USING GIN (commodity_groups)"
    )
    op.create_index("ix_vendor_participation", "vendor", ["participation"], schema=MKT)
    op.create_index("ix_vendor_tier", "vendor", ["tier"], schema=MKT)
    op.create_index("ix_vendor_country_of_origin", "vendor", ["country_of_origin"], schema=MKT)
    op.create_index("ix_vendor_active_status", "vendor", ["active_status"], schema=MKT)
    op.create_index(
        "ix_vendor_profile_completion_pct",
        "vendor",
        ["profile_completion_pct"],
        schema=MKT,
    )

    # ── vendor_part_capability (§2.15) ───────────────────────────────────────
    op.create_table(
        "vendor_part_capability",
        sa.Column(
            "capability_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_part_capability_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.part_master.part_id",
                ondelete="SET NULL",
                name="fk_vendor_part_capability_part_id_part_master",
            ),
            nullable=True,
        ),
        sa.Column("commodity_group", sa.String(128), nullable=True),
        sa.Column(
            "min_qty",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_qty", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "lead_time_band",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "price_band",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tooling_required",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "supported_finishes",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "supported_certifications",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "confidence_source",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'inferred'"),
        ),
        sa.Column("last_verified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "data_freshness_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence_source IN ('historical','declared','inferred')",
            name="ck_vendor_part_capability_confidence_source",
        ),
        sa.CheckConstraint(
            "min_qty >= 0", name="ck_vendor_part_capability_min_qty_nonneg"
        ),
        sa.CheckConstraint(
            "part_id IS NOT NULL OR commodity_group IS NOT NULL",
            name="ck_vendor_part_capability_at_least_one_scope",
        ),
        schema=MKT,
    )
    op.create_index(
        "ix_vendor_part_capability_vendor_id_part_id",
        "vendor_part_capability",
        ["vendor_id", "part_id"],
        schema=MKT,
    )
    op.create_index(
        "ix_vendor_part_capability_vendor_id_commodity_group",
        "vendor_part_capability",
        ["vendor_id", "commodity_group"],
        schema=MKT,
    )
    op.create_index(
        "ix_vendor_part_capability_confidence_source",
        "vendor_part_capability",
        ["confidence_source"],
        schema=MKT,
    )

    # ── vendor_performance_snapshot (§2.16) ───────────────────────────────────
    op.create_table(
        "vendor_performance_snapshot",
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_performance_snapshot_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("on_time_delivery_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("defect_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("response_speed_avg", sa.Numeric(10, 2), nullable=True),
        sa.Column("quote_accuracy", sa.Numeric(5, 4), nullable=True),
        sa.Column("doc_completeness", sa.Numeric(5, 4), nullable=True),
        sa.Column("ncr_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("orders_in_window", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("window_days", sa.Integer, nullable=False, server_default=sa.text("90")),
        sa.Column(
            "rebuilt_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_monthly_archive",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "on_time_delivery_rate BETWEEN 0 AND 1 OR on_time_delivery_rate IS NULL",
            name="ck_vendor_performance_snapshot_on_time_delivery_rate_range",
        ),
        sa.CheckConstraint(
            "defect_rate BETWEEN 0 AND 1 OR defect_rate IS NULL",
            name="ck_vendor_performance_snapshot_defect_rate_range",
        ),
        schema=MKT,
    )
    op.create_index(
        "uq_vendor_performance_snapshot_vendor_id_snapshot_date_is_monthly_archive",
        "vendor_performance_snapshot",
        ["vendor_id", "snapshot_date", "is_monthly_archive"],
        unique=True,
        schema=MKT,
    )
    op.create_index(
        "ix_vendor_performance_snapshot_vendor_id_rebuilt_at",
        "vendor_performance_snapshot",
        ["vendor_id", "rebuilt_at"],
        schema=MKT,
    )

    # ── certification (§2.17) ────────────────────────────────────────────────
    op.create_table(
        "certification",
        sa.Column(
            "certification_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_certification_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("custom_type_label", sa.String(128), nullable=True),
        sa.Column("document_url", sa.String(1024), nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column(
            "verified_by_pgi",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'UPLOADED'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "type IN ('ISO_9001','ISO_14001','ISO_45001','RoHS','REACH','CE',"
            "'IATF_16949','AS9100','custom')",
            name="ck_certification_type",
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADED','UNDER_REVIEW','VERIFIED','EXPIRED','REJECTED')",
            name="ck_certification_status",
        ),
        schema=MKT,
    )
    op.create_index(
        "ix_certification_vendor_id_status",
        "certification",
        ["vendor_id", "status"],
        schema=MKT,
    )
    op.create_index(
        "ix_certification_expiry_date", "certification", ["expiry_date"], schema=MKT
    )

    # ── vendor_profile_claim (§2.18) ─────────────────────────────────────────
    op.create_table(
        "vendor_profile_claim",
        sa.Column(
            "claim_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_profile_claim_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("claimant_email", sa.String(320), nullable=False),
        sa.Column(
            "claimant_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="SET NULL",
                name="fk_vendor_profile_claim_claimant_user_id_user",
            ),
            nullable=True,
        ),
        sa.Column("registration_number", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'INITIATED'"),
        ),
        sa.Column(
            "verification_evidence_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('INITIATED','EMAIL_VERIFIED','BUSINESS_VERIFIED','APPROVED','REJECTED')",
            name="ck_vendor_profile_claim_status",
        ),
        schema=MKT,
    )

    # ── vendor_invite (§2.19) ────────────────────────────────────────────────
    op.create_table(
        "vendor_invite",
        sa.Column(
            "invite_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_invite_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_vendor_invite_invited_by_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column("invitee_email", sa.String(320), nullable=False),
        sa.Column("unique_token", sa.String(128), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=MKT,
    )
    op.create_index(
        "uq_vendor_invite_unique_token",
        "vendor_invite",
        ["unique_token"],
        unique=True,
        schema=MKT,
    )

    # ── vendor_tier_transition (§2.20) — APPEND-ONLY ──────────────────────────
    op.create_table(
        "vendor_tier_transition",
        sa.Column(
            "transition_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_tier_transition_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("from_tier", sa.String(16), nullable=False),
        sa.Column("to_tier", sa.String(16), nullable=False),
        sa.Column("triggered_by", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "triggered_by IN ('system','admin','vendor')",
            name="ck_vendor_tier_transition_triggered_by",
        ),
        schema=MKT,
    )
    op.create_index(
        "ix_vendor_tier_transition_vendor_id",
        "vendor_tier_transition",
        ["vendor_id"],
        schema=MKT,
    )

    # ── finalize vendor_user.vendor_id FK (table created in 0002) ────────────
    op.create_foreign_key(
        "fk_vendor_user_vendor_id_vendor",
        "vendor_user",
        "vendor",
        ["vendor_id"],
        ["vendor_id"],
        source_schema=IDN,
        referent_schema=MKT,
        ondelete="CASCADE",
    )

    # ── finalize preferred_vendor_member.vendor_id FK (table in 0003) ────────
    op.create_foreign_key(
        "fk_preferred_vendor_member_vendor_id_vendor",
        "preferred_vendor_member",
        "vendor",
        ["vendor_id"],
        ["vendor_id"],
        source_schema=MKT,
        referent_schema=MKT,
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_preferred_vendor_member_vendor_id_vendor",
        "preferred_vendor_member",
        schema=MKT,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_vendor_user_vendor_id_vendor",
        "vendor_user",
        schema=IDN,
        type_="foreignkey",
    )
    op.drop_table("vendor_tier_transition", schema=MKT)
    op.drop_table("vendor_invite", schema=MKT)
    op.drop_table("vendor_profile_claim", schema=MKT)
    op.drop_table("certification", schema=MKT)
    op.drop_table("vendor_performance_snapshot", schema=MKT)
    op.drop_table("vendor_part_capability", schema=MKT)
    op.drop_table("vendor", schema=MKT)