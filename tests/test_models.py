"""Tests for model registration and basic model functionality."""
import pytest


def test_all_models_registered_with_metadata():
    from app.core.database import Base
    from app.models import *  # noqa: F401, F403

    table_names = set(Base.metadata.tables.keys())

    expected_tables = {
        "auth.organizations",
        "auth.users",
        "auth.organization_memberships",
        "auth.guest_sessions",
        "auth.vendor_users",
        "bom.boms",
        "bom.bom_parts",
        "bom.analysis_results",
        "projects.projects",
        "projects.project_acl",
        "projects.project_events",
        "projects.search_sessions",
        "projects.sourcing_cases",
        "pricing.vendors",
        "pricing.vendor_capabilities",
        "pricing.vendor_match_runs",
        "pricing.vendor_matches",
        "pricing.vendor_performance_snapshots",
        "sourcing.rfq_batches",
        "sourcing.rfq_items",
        "sourcing.rfq_vendor_invitations",
        "sourcing.invitation_status_events",
        "sourcing.rfq_quote_headers",
        "sourcing.rfq_quote_lines",
        "sourcing.purchase_orders",
        "sourcing.po_line_items",
        "sourcing.approval_requests",
        "finance.invoices",
        "finance.invoice_lines",
        "finance.payments",
        "finance.goods_receipts",
        "finance.goods_receipt_lines",
        "logistics.shipments",
        "logistics.shipment_milestones",
        "ops.platform_events",
        "ops.event_audit_log",
        "ops.idempotency_records",
        "ops.report_snapshots",
        "ops.notifications",
        "ops.notification_preferences",
        "ops.integration_run_logs",
        "ops.chat_threads",
        "ops.chat_messages",
        "market.fx_rates",
        "market.freight_rates",
        "market.tariff_schedules",
        "market.commodity_indices",
    }

    for tbl in expected_tables:
        assert tbl in table_names, f"Missing table: {tbl}"


def test_user_belongs_to_organization(db_session, test_user, test_org):
    assert test_user.organization_id == test_org.id


def test_guest_session_expiry_field(db_session, test_guest_session):
    assert test_guest_session.expires_at is not None
    assert test_guest_session.status == "ACTIVE"
