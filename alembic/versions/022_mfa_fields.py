"""MFA fields on users (Blueprint §31.1).
Revision ID: 022
Revises: 021
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"

def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret_enc TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_backup_codes_enc TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_reason TEXT")

def downgrade():
    pass
