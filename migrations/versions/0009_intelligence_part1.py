"""0009 — Intelligence domain part 1: normalization_run, normalization_trace,
candidate_match, review_task.

Revision ID: 0009
Revises: 0008
Create Date: 2024-01-01

Contract anchors:
  §2.9  Normalization_Run      §2.10 Normalization_Trace (APPEND-ONLY)
  §2.11 Candidate_Match        §2.12 Review_Task
  §3.30 Normalization_Run.status  §3.16 Review_Task.status  §3.73 decision_type
  CN-15: merged_with_bom_line_ids NOT stored as UUID[]; use join table in 0024.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTEL = "intelligence"
WS = "workspace"
IDN = "identity"


def upgrade() -> None:

    # ── normalization_run (§2.9) ─────────────────────────────────────────────
    op.create_table(
        "normalization_run",
        sa.Column(
            "run_id",
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
                name="fk_normalization_run_project_id_project",
            ),
            nullable=False,
        ),
        sa.Column("nlp_model_version", sa.String(32), nullable=False),
        sa.Column(
            "input_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'QUEUED'"),
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="ck_normalization_run_status",
        ),
        sa.CheckConstraint("input_count >= 0", name="ck_normalization_run_input_count_nonneg"),
        sa.CheckConstraint(
            "output_count >= 0", name="ck_normalization_run_output_count_nonneg"
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_normalization_run_project_id_started_at",
        "normalization_run",
        ["project_id", "started_at"],
        schema=INTEL,
    )

    # ── normalization_trace (§2.10) — APPEND-ONLY ────────────────────────────
    # CN-15: merged_with_bom_line_ids NOT stored here; join table in 0024
    op.create_table(
        "normalization_trace",
        sa.Column(
            "trace_id",
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
                name="fk_normalization_trace_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "normalization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.normalization_run.run_id",
                ondelete="RESTRICT",
                name="fk_normalization_trace_normalization_run_id_normalization_run",
            ),
            nullable=False,
        ),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column(
            "canonical_output_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("decision_type", sa.String(16), nullable=False),
        sa.Column("nlp_model_version", sa.String(32), nullable=False),
        sa.Column(
            "part_master_candidates_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "ambiguity_flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "split_from_bom_line_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{WS}.bom_line.bom_line_id",
                ondelete="SET NULL",
                name="fk_normalization_trace_split_from_bom_line_id_bom_line",
            ),
            nullable=True,
        ),
        # merged_with_bom_line_ids: NOT stored here per CN-15; see normalization_trace_merge join table (0024)
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_normalization_trace_confidence_range"
        ),
        sa.CheckConstraint(
            "decision_type IN ('auto','review_approved','review_edited','manual')",
            name="ck_normalization_trace_decision_type",
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_normalization_trace_bom_line_id_created_at",
        "normalization_trace",
        ["bom_line_id", "created_at"],
        schema=INTEL,
    )
    op.create_index(
        "ix_normalization_trace_normalization_run_id",
        "normalization_trace",
        ["normalization_run_id"],
        schema=INTEL,
    )

    # ── candidate_match (§2.11) ───────────────────────────────────────────────
    op.create_table(
        "candidate_match",
        sa.Column(
            "match_id",
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
                name="fk_candidate_match_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{INTEL}.part_master.part_id",
                ondelete="RESTRICT",
                name="fk_candidate_match_part_id_part_master",
            ),
            nullable=False,
        ),
        sa.Column("similarity_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("match_rank", sa.Integer, nullable=False),
        sa.Column("embedding_distance", sa.Numeric(10, 6), nullable=True),
        sa.Column("token_overlap", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "similarity_score BETWEEN 0 AND 1",
            name="ck_candidate_match_similarity_score_range",
        ),
        sa.CheckConstraint("match_rank >= 1", name="ck_candidate_match_match_rank_pos"),
        schema=INTEL,
    )
    op.create_index(
        "ix_candidate_match_bom_line_id_match_rank",
        "candidate_match",
        ["bom_line_id", "match_rank"],
        schema=INTEL,
    )

    # ── review_task (§2.12) ───────────────────────────────────────────────────
    op.create_table(
        "review_task",
        sa.Column(
            "task_id",
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
                name="fk_review_task_bom_line_id_bom_line",
            ),
            nullable=False,
        ),
        sa.Column(
            "assigned_to",
            UUID(as_uuid=True),
            sa.ForeignKey(
                f"{IDN}.user.user_id",
                ondelete="SET NULL",
                name="fk_review_task_assigned_to_user",
            ),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column(
            "flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','RESOLVED','ABANDONED')",
            name="ck_review_task_status",
        ),
        schema=INTEL,
    )
    op.create_index(
        "ix_review_task_status_created_at",
        "review_task",
        ["status", "created_at"],
        schema=INTEL,
    )
    op.create_index(
        "ix_review_task_assigned_to_status",
        "review_task",
        ["assigned_to", "status"],
        schema=INTEL,
    )


def downgrade() -> None:
    op.drop_table("review_task", schema=INTEL)
    op.drop_table("candidate_match", schema=INTEL)
    op.drop_table("normalization_trace", schema=INTEL)
    op.drop_table("normalization_run", schema=INTEL)