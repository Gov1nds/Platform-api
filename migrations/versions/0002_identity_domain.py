"""0002 — Identity domain: organization, user, org_membership, oauth_link,
refresh_token, mfa_enrollment, vendor_user.

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-01

Contract anchors:
  §2.2  User               §2.3  Organization          §2.14 VendorUser
  §2.80 OAuthLink          §2.81 RefreshToken          §2.82 MFAEnrollment
  §2.83 OrganizationMembership
  §3.21-§3.24, §3.82-§3.83, §3.88

Notes:
  - organization.preferred_vendor_list_id FK is intentionally omitted here
    (circular dependency); added in 0003 via ALTER TABLE.
  - vendor_user.vendor_id FK added in 0007 once vendor table exists.
  - All status/role columns use VARCHAR + CHECK (no PostgreSQL ENUM TYPE, §2.1).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "identity"


def upgrade() -> None:

    # ── organization (§2.3) ──────────────────────────────────────────────────
    op.create_table(
        "organization",
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column(
            "default_country",
            sa.String(2),
            nullable=False,
            server_default=sa.text("'US'"),
        ),
        sa.Column(
            "default_currency",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column(
            "billing_plan",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'FREE'"),
        ),
        sa.Column(
            "compliance_profile",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "auto_order_threshold",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # preferred_vendor_list_id added in 0003
        sa.Column(
            "report_cadence",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "approval_chain_json",
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
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "billing_plan IN ('FREE','STARTER','PRO','ENTERPRISE')",
            name="ck_organization_billing_plan",
        ),
        sa.CheckConstraint(
            "char_length(default_country) = 2",
            name="ck_organization_default_country_iso3166",
        ),
        sa.CheckConstraint(
            "char_length(default_currency) = 3",
            name="ck_organization_default_currency_iso4217",
        ),
        sa.CheckConstraint(
            "auto_order_threshold >= 0",
            name="ck_organization_auto_order_threshold_nonneg",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_billing_plan",
        "organization",
        ["billing_plan"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_organization_default_country",
        "organization",
        ["default_country"],
        schema=SCHEMA,
    )

    # ── user (§2.2) ──────────────────────────────────────────────────────────
    op.create_table(
        "user",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.organization.organization_id",
                ondelete="RESTRICT",
                name="fk_user_organization_id_organization",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "role",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'buyer_viewer'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'PENDING_VERIFICATION'"),
        ),
        sa.Column(
            "locale",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'en-US'"),
        ),
        sa.Column(
            "currency_preference",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
        sa.Column(
            "approval_level",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("department", sa.String(128), nullable=True),
        sa.Column("cost_center", sa.String(64), nullable=True),
        sa.Column("detected_country", sa.String(2), nullable=True),
        sa.Column("detected_currency", sa.String(3), nullable=True),
        sa.Column("priority_sensitivity", sa.String(16), nullable=True),
        sa.Column(
            "mfa_enrolled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_active_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            "role IN ('owner','admin','approver','buyer_editor','buyer_viewer',"
            "'vendor_rep','vendor_admin','pgi_admin')",
            name="ck_user_role",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_VERIFICATION','ACTIVE','SUSPENDED','DEACTIVATED','DELETED')",
            name="ck_user_status",
        ),
        sa.CheckConstraint(
            "priority_sensitivity IN ('cost','speed','quality','compliance') "
            "OR priority_sensitivity IS NULL",
            name="ck_user_priority_sensitivity",
        ),
        sa.CheckConstraint("approval_level >= 0", name="ck_user_approval_level_nonneg"),
        sa.CheckConstraint(
            "char_length(currency_preference) = 3",
            name="ck_user_currency_preference_iso4217",
        ),
        sa.CheckConstraint(
            "email = lower(email)", name="ck_user_email_lowercase"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_user_organization_id", "user", ["organization_id"], schema=SCHEMA)
    op.create_index(
        "uq_user_email", "user", ["email"], unique=True, schema=SCHEMA
    )
    op.create_index("ix_user_status", "user", ["status"], schema=SCHEMA)
    op.create_index("ix_user_last_active_at", "user", ["last_active_at"], schema=SCHEMA)

    # ── organization_membership (§2.83) ──────────────────────────────────────
    op.create_table(
        "organization_membership",
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.organization.organization_id",
                ondelete="RESTRICT",
                name="fk_organization_membership_organization_id_organization",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.user.user_id",
                ondelete="RESTRICT",
                name="fk_organization_membership_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "joined_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "user_id", name="pk_organization_membership"
        ),
        schema=SCHEMA,
    )

    # ── oauth_link (§2.80) ───────────────────────────────────────────────────
    op.create_table(
        "oauth_link",
        sa.Column(
            "link_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.user.user_id",
                ondelete="CASCADE",
                name="fk_oauth_link_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_subject", sa.String(256), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "provider IN ('google','linkedin','microsoft','saml')",
            name="ck_oauth_link_provider",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_oauth_link_user_id", "oauth_link", ["user_id"], schema=SCHEMA)
    op.create_index(
        "uq_oauth_link_provider_provider_subject",
        "oauth_link",
        ["provider", "provider_subject"],
        unique=True,
        schema=SCHEMA,
    )

    # ── refresh_token (§2.81) ────────────────────────────────────────────────
    op.create_table(
        "refresh_token",
        sa.Column(
            "token_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.user.user_id",
                ondelete="CASCADE",
                name="fk_refresh_token_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column(sa.CHAR(64), name="token_hash", nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(sa.CHAR(64), name="ip_hash", nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_refresh_token_token_hash",
        "refresh_token",
        ["token_hash"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_refresh_token_user_id", "refresh_token", ["user_id"], schema=SCHEMA
    )

    # ── mfa_enrollment (§2.82) ───────────────────────────────────────────────
    op.create_table(
        "mfa_enrollment",
        sa.Column(
            "enrollment_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.user.user_id",
                ondelete="CASCADE",
                name="fk_mfa_enrollment_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("secret_encrypted", sa.LargeBinary, nullable=True),
        sa.Column(
            "enrolled_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "method IN ('totp','sms','webauthn')", name="ck_mfa_enrollment_method"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mfa_enrollment_user_id", "mfa_enrollment", ["user_id"], schema=SCHEMA
    )

    # ── vendor_user (§2.14) ─────────────────────────────────────────────────
    # vendor_id FK deferred to 0007 (vendor table not yet created)
    op.create_table(
        "vendor_user",
        sa.Column(
            "vendor_user_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'vendor_rep'"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'PENDING_VERIFICATION'"),
        ),
        sa.Column(
            "mfa_enrolled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_active_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "role IN ('vendor_rep','vendor_admin')", name="ck_vendor_user_role"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_VERIFICATION','ACTIVE','SUSPENDED','DEACTIVATED')",
            name="ck_vendor_user_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_vendor_user_vendor_id_email",
        "vendor_user",
        ["vendor_id", "email"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_vendor_user_vendor_id", "vendor_user", ["vendor_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("vendor_user", schema=SCHEMA)
    op.drop_table("mfa_enrollment", schema=SCHEMA)
    op.drop_table("refresh_token", schema=SCHEMA)
    op.drop_table("oauth_link", schema=SCHEMA)
    op.drop_table("organization_membership", schema=SCHEMA)
    op.drop_table("user", schema=SCHEMA)
    op.drop_table("organization", schema=SCHEMA)