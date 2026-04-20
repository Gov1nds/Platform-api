"""0004 — Workspace domain: project, project_acl, workspace_decision,
bom_upload, bom_line.

Revision ID: 0004
Revises: 0003
Create Date: 2024-01-01

Contract anchors:
  §2.4  Project         §2.5  ProjectACL        §2.91 WorkspaceDecision
  §2.6  BOM_Upload      §2.7  BOM_Line
  §3.2  Project.state (SM-002)  §3.3  is_session_or_project
  §3.4  BOM_Upload.import_status  §3.1  BOM_Line.status (SM-001)
  §3.46-§3.48 various Project enums

Notes:
  - bom_line.part_id FK (→ intelligence.part_master) added in 0006.
  - BOM_Line.score_cache_json is a denormalized read-cache only (§2.93, CN-20).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WS = "workspace"
IDN = "identity"


def upgrade() -> None:

    # ── project (§2.4) ───────────────────────────────────────────────────────
    op.create_table(
        "project",
        sa.Column(
            "project_id",
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
                name="fk_project_organization_id_organization",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_project_created_by_user",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("target_country", sa.String(2), nullable=True),
        sa.Column("target_location", sa.String(255), nullable=True),
        sa.Column("delivery_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("delivery_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column(
            "target_currency",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column(
            "priority",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'NORMAL'"),
        ),
        sa.Column("stage", sa.String(16), nullable=True),
        sa.Column(
            "weight_profile",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'balanced'"),
        ),
        sa.Column("weight_profile_custom_json", JSONB, nullable=True),
        sa.Column(
            "is_session_or_project",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'session'"),
        ),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'DRAFT'"),
        ),
        sa.Column("required_timeline", sa.Interval, nullable=True),
        sa.Column("incoterm_preference", sa.String(16), nullable=True),
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
            "priority IN ('LOW','NORMAL','HIGH','URGENT')",
            name="ck_project_priority",
        ),
        sa.CheckConstraint(
            "stage IN ('prototype','pilot','production') OR stage IS NULL",
            name="ck_project_stage",
        ),
        sa.CheckConstraint(
            "weight_profile IN ('speed_first','cost_first','quality_first','balanced','custom')",
            name="ck_project_weight_profile",
        ),
        sa.CheckConstraint(
            "is_session_or_project IN ('session','project')",
            name="ck_project_is_session_or_project",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','INTAKE_COMPLETE','ANALYSIS_IN_PROGRESS',"
            "'ANALYSIS_COMPLETE','SOURCING_ACTIVE','ORDERING_IN_PROGRESS',"
            "'EXECUTION_ACTIVE','PARTIALLY_DELIVERED','FULLY_DELIVERED',"
            "'CLOSED','CANCELLED','ARCHIVED')",
            name="ck_project_state",
        ),
        sa.CheckConstraint(
            "(weight_profile = 'custom' AND weight_profile_custom_json IS NOT NULL) "
            "OR weight_profile <> 'custom'",
            name="ck_project_weight_profile_custom_json_required",
        ),
        sa.CheckConstraint(
            "(is_session_or_project = 'project' AND name IS NOT NULL) "
            "OR is_session_or_project = 'session'",
            name="ck_project_name_required_for_project",
        ),
        schema=WS,
    )
    op.create_index(
        "ix_project_organization_id_state",
        "project",
        ["organization_id", "state"],
        schema=WS,
    )
    op.create_index(
        "ix_project_created_by", "project", ["created_by"], schema=WS
    )
    op.create_index(
        "ix_project_is_session_or_project",
        "project",
        ["is_session_or_project"],
        schema=WS,
    )
    op.create_index(
        "ix_project_organization_id_is_session_or_project",
        "project",
        ["organization_id", "is_session_or_project"],
        schema=WS,
    )

    # ── project_acl (§2.5) ───────────────────────────────────────────────────
    op.create_table(
        "project_acl",
        sa.Column(
            "acl_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.project.project_id",
                ondelete="CASCADE",
                name="fk_project_acl_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_project_acl_user_id_user",
            ),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "granted_by",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_project_acl_granted_by_user",
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner','viewer','approver','editor')",
            name="ck_project_acl_role",
        ),
        schema=WS,
    )
    op.create_index(
        "uq_project_acl_project_id_user_id",
        "project_acl",
        ["project_id", "user_id"],
        unique=True,
        schema=WS,
    )
    op.create_index("ix_project_acl_user_id", "project_acl", ["user_id"], schema=WS)

    # ── workspace_decision (§2.91) ────────────────────────────────────────────
    op.create_table(
        "workspace_decision",
        sa.Column(
            "decision_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.project.project_id",
                ondelete="CASCADE",
                name="fk_workspace_decision_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="RESTRICT",
                name="fk_workspace_decision_decided_by_user",
            ),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(8), nullable=False),
        sa.Column("to_state", sa.String(8), nullable=False),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "from_state IN ('session','project')",
            name="ck_workspace_decision_from_state",
        ),
        sa.CheckConstraint(
            "to_state IN ('session','project')",
            name="ck_workspace_decision_to_state",
        ),
        schema=WS,
    )

    # ── bom_upload (§2.6) ────────────────────────────────────────────────────
    op.create_table(
        "bom_upload",
        sa.Column(
            "upload_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.project.project_id",
                ondelete="CASCADE",
                name="fk_bom_upload_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=True),
        sa.Column(sa.CHAR(64), name="file_hash", nullable=True),
        sa.Column(
            "import_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'RECEIVED'"),
        ),
        sa.Column(
            "validation_errors_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "row_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "column_mapping_json",
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
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('csv','xlsx','typed','single_search')",
            name="ck_bom_upload_source_type",
        ),
        sa.CheckConstraint(
            "import_status IN ('RECEIVED','PARSING','PARTIAL','COMPLETE','FAILED')",
            name="ck_bom_upload_import_status",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_bom_upload_row_count_nonneg"),
        schema=WS,
    )
    op.create_index(
        "uq_bom_upload_project_id_file_hash",
        "bom_upload",
        ["project_id", "file_hash"],
        unique=True,
        postgresql_where=sa.text("file_hash IS NOT NULL"),
        schema=WS,
    )
    op.create_index(
        "ix_bom_upload_import_status",
        "bom_upload",
        ["import_status"],
        schema=WS,
    )

    # ── bom_line (§2.7) ─────────────────────────────────────────────────────
    # part_id FK added in 0006 after part_master table is created.
    op.create_table(
        "bom_line",
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.project.project_id",
                ondelete="CASCADE",
                name="fk_bom_line_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column(
            "upload_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_upload.upload_id",
                ondelete="SET NULL",
                name="fk_bom_line_upload_id_bom_upload",
            ),
            nullable=True,
        ),
        # part_id FK added in 0006
        sa.Column("part_id", UUID(as_uuid=True), nullable=True),
        sa.Column("row_number", sa.Integer, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("normalized_name", sa.String(512), nullable=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column(
            "spec_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "quantity",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("target_country", sa.String(2), nullable=True),
        sa.Column("delivery_location", sa.String(255), nullable=True),
        sa.Column(
            "priority",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'NORMAL'"),
        ),
        sa.Column(
            "acceptable_substitutes",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "required_certifications",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("manufacturer_part_number", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'RAW'"),
        ),
        sa.Column("sourcing_type", sa.String(24), nullable=True),
        sa.Column("normalization_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "enrichment_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Denormalized read-cache only per §2.93, CN-20; authoritative in vendor_score_cache
        sa.Column(
            "score_cache_json",
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
        sa.CheckConstraint(
            "status IN ('RAW','NORMALIZING','NORMALIZED','NEEDS_REVIEW','ENRICHING',"
            "'ENRICHED','SCORING','SCORED','RFQ_PENDING','RFQ_SENT','QUOTED',"
            "'AWARDED','ORDERED','DELIVERED','CLOSED','CANCELLED','ERROR')",
            name="ck_bom_line_status",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW','NORMAL','HIGH','URGENT')",
            name="ck_bom_line_priority",
        ),
        sa.CheckConstraint(
            "sourcing_type IN ('local_direct','international_direct','distributor',"
            "'broker','contract_manufacturer') OR sourcing_type IS NULL",
            name="ck_bom_line_sourcing_type",
        ),
        sa.CheckConstraint(
            "normalization_confidence BETWEEN 0 AND 1 OR normalization_confidence IS NULL",
            name="ck_bom_line_normalization_confidence_range",
        ),
        schema=WS,
    )
    op.create_index(
        "ix_bom_line_project_id_status",
        "bom_line",
        ["project_id", "status"],
        schema=WS,
    )
    op.create_index("ix_bom_line_upload_id", "bom_line", ["upload_id"], schema=WS)
    op.create_index("ix_bom_line_part_id", "bom_line", ["part_id"], schema=WS)
    # GIN index on spec_json for JSONB queries
    op.execute(
        "CREATE INDEX ix_bom_line_spec_json_gin ON workspace.bom_line USING GIN (spec_json)"
    )


def downgrade() -> None:
    op.drop_table("bom_line", schema=WS)
    op.drop_table("bom_upload", schema=WS)
    op.drop_table("workspace_decision", schema=WS)
    op.drop_table("project_acl", schema=WS)
    op.drop_table("project", schema=WS)