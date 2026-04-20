"""0006 — Add part_id FK from workspace.bom_line to intelligence.part_master.

Revision ID: 0006
Revises: 0005
Create Date: 2024-01-01

The column was created in 0004 without a FK constraint because part_master
did not exist yet. This migration adds the cross-schema FK now that both
tables exist.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_bom_line_part_id_part_master",
        "bom_line",
        "part_master",
        ["part_id"],
        ["part_id"],
        source_schema="workspace",
        referent_schema="intelligence",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_bom_line_part_id_part_master",
        "bom_line",
        schema="workspace",
        type_="foreignkey",
    )