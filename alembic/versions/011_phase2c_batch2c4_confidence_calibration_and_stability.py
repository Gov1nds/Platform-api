"""011 phase2c batch2c4 confidence calibration and recommendation stability

Revision ID: 011_phase2c_batch2c4_confidence_calibration_and_stability
Revises: 010_phase2c_batch2c3_anomaly_detection
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "011_phase2c_batch2c4_confidence_calibration_and_stability"
down_revision = "010_phase2c_batch2c3_anomaly_detection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "confidence_calibration_data",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("calibration_id", sa.String(length=128), nullable=False),
        sa.Column("score_range_min", sa.Numeric(12, 6), nullable=False),
        sa.Column("score_range_max", sa.Numeric(12, 6), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("historical_success_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("calibrated_probability", sa.Numeric(12, 6), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="pricing",
    )
    op.create_index("ix_confidence_calibration_calculated", "confidence_calibration_data", ["calculated_at"], schema="pricing")
    op.create_index("ix_confidence_calibration_range", "confidence_calibration_data", ["score_range_min", "score_range_max"], schema="pricing")


def downgrade() -> None:
    op.drop_index("ix_confidence_calibration_range", table_name="confidence_calibration_data", schema="pricing")
    op.drop_index("ix_confidence_calibration_calculated", table_name="confidence_calibration_data", schema="pricing")
    op.drop_table("confidence_calibration_data", schema="pricing")