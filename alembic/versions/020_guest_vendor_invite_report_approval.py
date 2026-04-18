"""Guest_Search_Log, Vendor_Invite_Token, Report_Snapshot, Approval_Chain.
Revision ID: 020
Revises: 019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "020"
down_revision = "019"

def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS guest_search_log (
            search_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID NOT NULL,
            search_query TEXT NOT NULL,
            components_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            detected_country CHAR(2),
            detected_currency CHAR(3),
            delivery_location_json JSONB,
            vendor_results_json JSONB,
            free_report_generated BOOLEAN NOT NULL DEFAULT FALSE,
            converted_to_signup BOOLEAN NOT NULL DEFAULT FALSE,
            converted_user_id UUID,
            ip_address INET,
            user_agent TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gsl_session ON guest_search_log (session_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gsl_converted ON guest_search_log (converted_user_id) WHERE converted_user_id IS NOT NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS vendor_invite_token (
            token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id UUID NOT NULL,
            email TEXT NOT NULL,
            invited_by_user_id UUID,
            token_hash TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_vit_vendor ON vendor_invite_token (vendor_id, purpose)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vit_email_active ON vendor_invite_token (email) WHERE consumed_at IS NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS report_snapshot (
            snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            report_type TEXT NOT NULL,
            period_start TIMESTAMPTZ,
            period_end TIMESTAMPTZ,
            filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            payload_json JSONB NOT NULL,
            rendered_pdf_url TEXT,
            rendered_xlsx_url TEXT,
            ai_insight_text TEXT,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rs_org_type_period ON report_snapshot (organization_id, report_type, period_end DESC)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_chain (
            chain_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            name TEXT NOT NULL,
            rules_json JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS approval_chain")
    op.execute("DROP TABLE IF EXISTS report_snapshot")
    op.execute("DROP TABLE IF EXISTS vendor_invite_token")
    op.execute("DROP TABLE IF EXISTS guest_search_log")
