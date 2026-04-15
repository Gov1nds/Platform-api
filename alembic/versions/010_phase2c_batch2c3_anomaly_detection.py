"""010 phase2c batch2c3 anomaly detection and anomaly flagging

Revision ID: 010_phase2c_batch2c3_anomaly_detection
Revises: 009_phase2c_batch2c2_lead_time_intelligence
Create Date: 2026-04-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "010_phase2c_batch2c3_anomaly_detection"
down_revision = "009_phase2c_batch2c2_lead_time_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anomaly_flags",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("anomaly_id", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("observed_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("threshold_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("anomaly_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_context_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dedupe_window_key", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("anomaly_id", name="uq_anomaly_flags_anomaly_id"),
        sa.UniqueConstraint("dedupe_window_key", name="uq_anomaly_flags_dedupe_window_key"),
        schema="ops",
    )
    op.create_index("ix_anomaly_flags_entity", "anomaly_flags", ["entity_type", "entity_id"], schema="ops")
    op.create_index("ix_anomaly_flags_metric_detected", "anomaly_flags", ["metric_name", "detected_at"], schema="ops")
    op.create_index("ix_anomaly_flags_severity_detected", "anomaly_flags", ["severity", "detected_at"], schema="ops")


def downgrade() -> None:
    op.drop_index("ix_anomaly_flags_severity_detected", table_name="anomaly_flags", schema="ops")
    op.drop_index("ix_anomaly_flags_metric_detected", table_name="anomaly_flags", schema="ops")
    op.drop_index("ix_anomaly_flags_entity", table_name="anomaly_flags", schema="ops")
    op.drop_table("anomaly_flags", schema="ops")