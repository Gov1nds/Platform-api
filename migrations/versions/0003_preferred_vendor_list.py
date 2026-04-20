"""0003 — marketplace.preferred_vendor_list, preferred_vendor_member;
add preferred_vendor_list_id FK to identity.organization.

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-01

Contract anchors:
  §2.22 Preferred_Vendor_List and Preferred_Vendor_Member
  §2.3  Organization.preferred_vendor_list_id (circular FK resolved here)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MKT = "marketplace"
IDN = "identity"


def upgrade() -> None:
    # ── preferred_vendor_list ────────────────────────────────────────────────
    op.create_table(
        "preferred_vendor_list",
        sa.Column(
            "list_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.organization.organization_id",
                ondelete="RESTRICT",
                name="fk_preferred_vendor_list_organization_id_organization",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=MKT,
    )
    op.create_index(
        "ix_preferred_vendor_list_organization_id",
        "preferred_vendor_list",
        ["organization_id"],
        schema=MKT,
    )

    # ── preferred_vendor_member ──────────────────────────────────────────────
    # vendor_id FK deferred to 0007 (vendor not yet created)
    op.create_table(
        "preferred_vendor_member",
        sa.Column(
            "list_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.preferred_vendor_list.list_id",
                ondelete="CASCADE",
                name="fk_preferred_vendor_member_list_id_preferred_vendor_list",
            ),
            nullable=False,
        ),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "added_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_preferred_vendor_member_added_by_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("list_id", "vendor_id", name="pk_preferred_vendor_member"),
        schema=MKT,
    )

    # ── add preferred_vendor_list_id FK to organization (circular resolved) ──
    op.add_column(
        "organization",
        sa.Column("preferred_vendor_list_id", UUID(as_uuid=True), nullable=True),
        schema=IDN,
    )
    op.create_foreign_key(
        "fk_organization_preferred_vendor_list_id_preferred_vendor_list",
        "organization",
        "preferred_vendor_list",
        ["preferred_vendor_list_id"],
        ["list_id"],
        source_schema=IDN,
        referent_schema=MKT,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_organization_preferred_vendor_list_id_preferred_vendor_list",
        "organization",
        schema=IDN,
        type_="foreignkey",
    )
    op.drop_column("organization", "preferred_vendor_list_id", schema=IDN)
    op.drop_table("preferred_vendor_member", schema=MKT)
    op.drop_table("preferred_vendor_list", schema=MKT)