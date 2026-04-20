"""0011 — intelligence.vendor_score_cache and intelligence.score_breakdown.

Revision ID: 0011
Revises: 0010
Create Date: 2024-01-01

Contract anchors:
  §2.24 Vendor_Score_Cache — keyed by (bom_line_id, vendor_id,
        weight_profile_hash, market_context_hash); TTL-based invalidation.
  §2.25 Score_Breakdown — denormalized per-dimension rows.
  §3.42 Vendor_Score_Cache.confidence (HIGH/MEDIUM/LOW)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTEL = "intelligence"
WS = "workspace"
MKT = "marketplace"


def upgrade() -> None:

    # ── vendor_score_cache (§2.24) ────────────────────────────────────────────
    op.create_table(
        "vendor_score_cache",
        sa.Column(
            "cache_id",
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
                name="fk_vendor_score_cache_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{MKT}.vendor.vendor_id",
                ondelete="CASCADE",
                name="fk_vendor_score_cache_vendor_id_vendor",
            ),
            nullable=False,
        ),
        sa.Column("total_score", sa.Numeric(6, 3), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column(sa.CHAR(64), name="weight_profile_hash", nullable=False),
        sa.Column(sa.CHAR(64), name="market_context_hash", nullable=False),
        sa.Column(
            "breakdown_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column(
            "data_sources_snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.data_sources_snapshot.snapshot_id",
                ondelete="RESTRICT",
                name="fk_vendor_score_cache_data_sources_snapshot_id_data_sources_snapshot",
            ),
            nullable=False,
        ),
        sa.Column("scoring_model_version", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ttl_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "total_score BETWEEN 0 AND 100",
            name="ck_vendor_score_cache_total_score_range",
        ),
        sa.CheckConstraint("rank >= 1", name="ck_vendor_score_cache_rank_pos"),
        sa.CheckConstraint(
            "confidence IN ('HIGH','MEDIUM','LOW')",
            name="ck_vendor_score_cache_confidence",
        ),
        schema=INTEL,
    )
    op.create_index(
        "uq_vendor_score_cache_bom_line_id_vendor_id_weight_profile_hash_market_context_hash",
        "vendor_score_cache",
        ["bom_line_id", "vendor_id", "weight_profile_hash", "market_context_hash"],
        unique=True,
        schema=INTEL,
    )
    op.create_index(
        "ix_vendor_score_cache_ttl_expires_at",
        "vendor_score_cache",
        ["ttl_expires_at"],
        schema=INTEL,
    )
    op.create_index(
        "ix_vendor_score_cache_bom_line_id_rank",
        "vendor_score_cache",
        ["bom_line_id", "rank"],
        schema=INTEL,
    )

    # ── score_breakdown (§2.25) ───────────────────────────────────────────────
    op.create_table(
        "score_breakdown",
        sa.Column(
            "breakdown_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "cache_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.vendor_score_cache.cache_id",
                ondelete="CASCADE",
                name="fk_score_breakdown_cache_id_vendor_score_cache",
            ),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(6, 3), nullable=False),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False),
        sa.Column("weighted_contribution", sa.Numeric(6, 3), nullable=False),
        sa.Column(
            "reasons_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "dimension IN ('cost_competitiveness','lead_time_availability',"
            "'quality_reliability','strategic_fit','operational_capability')",
            name="ck_score_breakdown_dimension",
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100", name="ck_score_breakdown_score_range"
        ),
        sa.CheckConstraint(
            "weight BETWEEN 0 AND 1", name="ck_score_breakdown_weight_range"
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_score_breakdown_cache_id",
        "score_breakdown",
        ["cache_id"],
        schema=INTEL,
    )


def downgrade() -> None:
    op.drop_table("score_breakdown", schema=INTEL)
    op.drop_table("vendor_score_cache", schema=INTEL)