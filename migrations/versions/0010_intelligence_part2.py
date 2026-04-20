"""0010 — Intelligence domain part 2: vendor_filter_result, evidence_record,
strategy_recommendation, substitution_recommendation, consolidation_insight,
data_sources_snapshot.

Revision ID: 0010
Revises: 0009
Create Date: 2024-01-01

Contract anchors:
  §2.23 Vendor_Filter_Result   §2.24 Vendor_Score_Cache (created in 0011)
  §2.25 Score_Breakdown        §2.26 Strategy_Recommendation
  §2.27 Substitution_Recommendation  §2.28 Consolidation_Insight
  §2.29 Data_Sources_Snapshot  §2.30 Evidence_Record
  CN-16: consolidation_insight.covered_bom_line_ids → join table in 0024
  CN-17: data_sources_snapshot UUID[] arrays → join table in 0024

Notes:
  - data_sources_snapshot.scoring_cache_id FK added in 0025 (circular with
    vendor_score_cache created in 0011).
  - vendor_score_cache table in 0011 will reference data_sources_snapshot.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTEL = "intelligence"
WS = "workspace"
MKT = "marketplace"
IDN = "identity"


def upgrade() -> None:

    # ── vendor_filter_result (§2.23) ─────────────────────────────────────────
    op.create_table(
        "vendor_filter_result",
        sa.Column(
            "result_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="CASCADE",
                name="fk_vendor_filter_result_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_filter_result_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("elimination_reason", sa.String(255), nullable=False),
        sa.Column("elimination_step", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "elimination_step IN ('hard_filter','technical_fit_below_threshold')",
            name="ck_vendor_filter_result_elimination_step",
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_vendor_filter_result_bom_line_id",
        "vendor_filter_result",
        ["bom_line_id"],
        schema=INTEL,
    )

    # ── evidence_record (§2.30) ───────────────────────────────────────────────
    op.create_table(
        "evidence_record",
        sa.Column(
            "evidence_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="CASCADE",
                name="fk_evidence_record_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column("data_point_type", sa.String(16), nullable=False),
        sa.Column(
            "value",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "freshness_status",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'FRESH'"),
        ),
        sa.CheckConstraint(
            "data_point_type IN ('price','lead_time','tariff','freight',"
            "'performance','certification','forex')",
            name="ck_evidence_record_data_point_type",
        ),
        sa.CheckConstraint(
            "freshness_status IN ('FRESH','STALE','EXPIRED','LOCKED')",
            name="ck_evidence_record_freshness_status",
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_evidence_record_bom_line_id_data_point_type",
        "evidence_record",
        ["bom_line_id", "data_point_type"],
        schema=INTEL,
    )
    op.create_index(
        "ix_evidence_record_freshness_status",
        "evidence_record",
        ["freshness_status"],
        schema=INTEL,
    )

    # ── strategy_recommendation (§2.26) ──────────────────────────────────────
    op.create_table(
        "strategy_recommendation",
        sa.Column(
            "recommendation_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="CASCADE",
                name="fk_strategy_recommendation_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column("recommended_mode", sa.String(32), nullable=False),
        sa.Column(
            "tlc_breakdown_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("q_break", sa.Numeric(20, 8), nullable=True),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "recommended_mode IN ('local_direct','international_direct','distributor',"
            "'broker','contract_manufacturer')",
            name="ck_strategy_recommendation_recommended_mode",
        ),
        schema=INTEL,
    )

    # ── substitution_recommendation (§2.27) ──────────────────────────────────
    op.create_table(
        "substitution_recommendation",
        sa.Column(
            "substitution_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="CASCADE",
                name="fk_substitution_recommendation_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "suggested_part_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.part_master.part_id",
                ondelete="RESTRICT",
                name="fk_substitution_recommendation_suggested_part_id_part_master",
            ),
            nullable=False,
        ),
        sa.Column(
            "spec_diff_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_substitution_recommendation_confidence_range",
        ),
        schema=INTEL,
    )

    # ── consolidation_insight (§2.28) ────────────────────────────────────────
    # CN-16: covered_bom_line_ids NOT stored as array; join table in 0024
    op.create_table(
        "consolidation_insight",
        sa.Column(
            "insight_id",
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
                name="fk_consolidation_insight_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="RESTRICT",
                name="fk_consolidation_insight_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column(
            "estimated_savings",
            sa.Numeric(20, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=INTEL,
    )

    # ── data_sources_snapshot (§2.29) ─────────────────────────────────────────
    # CN-17: source UUID arrays NOT stored; see data_sources_snapshot_link (0024)
    # scoring_cache_id FK added in 0025 (circular with vendor_score_cache)
    op.create_table(
        "data_sources_snapshot",
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # scoring_cache_id column added via ALTER in 0025
        sa.Column(
            "bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="CASCADE",
                name="fk_data_sources_snapshot_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_data_sources_snapshot_bom_line_id",
        "data_sources_snapshot",
        ["bom_line_id"],
        schema=INTEL,
    )


def downgrade() -> None:
    op.drop_table("data_sources_snapshot", schema=INTEL)
    op.drop_table("consolidation_insight", schema=INTEL)
    op.drop_table("substitution_recommendation", schema=INTEL)
    op.drop_table("strategy_recommendation", schema=INTEL)
    op.drop_table("evidence_record", schema=INTEL)
    op.drop_table("vendor_filter_result", schema=INTEL)