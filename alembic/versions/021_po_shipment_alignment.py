"""Align PO + Shipment with Blueprint §13 12-state machine.
Revision ID: 021
Revises: 020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "021"
down_revision = "020"

def upgrade():
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS incoterm TEXT")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS logistics_provider TEXT")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS tracking_number TEXT")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS approval_chain_id UUID")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS approval_state TEXT DEFAULT 'not_required'")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS split_source_group_id UUID")
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS last_state_transition_at TIMESTAMPTZ DEFAULT NOW()")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS carrier TEXT")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS service_level TEXT")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS milestone_history_json JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS eta TIMESTAMPTZ")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS last_carrier_update_at TIMESTAMPTZ")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS delay_flag BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE shipments ADD COLUMN IF NOT EXISTS delay_reason TEXT")
    op.execute("ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS confirmed_by_user_id UUID")
    op.execute("ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS discrepancy_notes TEXT")

def downgrade():
    pass
