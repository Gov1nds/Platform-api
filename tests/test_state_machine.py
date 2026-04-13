"""Tests for state machine transitions."""
import pytest
from app.enums import (
    ProjectStatus, BOMLineStatus, RFQStatus, QuoteStatus,
    POStatus, ShipmentStatus, InvoiceStatus, VendorStatus,
)
from app.services.workflow.state_machine import (
    PROJECT_TRANSITIONS, BOM_LINE_TRANSITIONS, BOM_UPLOAD_TRANSITIONS,
    can_transition,
)


class TestProjectSM002:
    """SM-002: Project Lifecycle — 12 states."""

    def test_project_has_12_states(self):
        assert len(ProjectStatus) == 12

    def test_happy_path_reachable(self):
        path = [
            ProjectStatus.DRAFT,
            ProjectStatus.INTAKE_COMPLETE,
            ProjectStatus.ANALYSIS_IN_PROGRESS,
            ProjectStatus.ANALYSIS_COMPLETE,
            ProjectStatus.SOURCING_ACTIVE,
            ProjectStatus.ORDERING_IN_PROGRESS,
            ProjectStatus.EXECUTION_ACTIVE,
            ProjectStatus.PARTIALLY_DELIVERED,
            ProjectStatus.FULLY_DELIVERED,
            ProjectStatus.CLOSED,
            ProjectStatus.ARCHIVED,
        ]
        for i in range(len(path) - 1):
            assert (path[i], path[i + 1]) in PROJECT_TRANSITIONS, \
                f"Missing transition {path[i]} -> {path[i+1]}"

    def test_cancellation_from_active_states(self):
        cancellable = [
            ProjectStatus.DRAFT,
            ProjectStatus.INTAKE_COMPLETE,
            ProjectStatus.ANALYSIS_IN_PROGRESS,
            ProjectStatus.ANALYSIS_COMPLETE,
            ProjectStatus.SOURCING_ACTIVE,
            ProjectStatus.ORDERING_IN_PROGRESS,
            ProjectStatus.EXECUTION_ACTIVE,
            ProjectStatus.PARTIALLY_DELIVERED,
        ]
        for state in cancellable:
            assert (state, ProjectStatus.CANCELLED) in PROJECT_TRANSITIONS

    def test_cannot_cancel_from_terminal(self):
        assert (ProjectStatus.CLOSED, ProjectStatus.CANCELLED) not in PROJECT_TRANSITIONS
        assert (ProjectStatus.CANCELLED, ProjectStatus.CANCELLED) not in PROJECT_TRANSITIONS
        assert (ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED) not in PROJECT_TRANSITIONS

    def test_archive_only_from_closed(self):
        assert (ProjectStatus.CLOSED, ProjectStatus.ARCHIVED) in PROJECT_TRANSITIONS
        assert (ProjectStatus.DRAFT, ProjectStatus.ARCHIVED) not in PROJECT_TRANSITIONS

    def test_invalid_transition_detected(self):
        assert not can_transition(ProjectStatus.DRAFT, ProjectStatus.CLOSED)


class TestBOMLineSM001:
    """SM-001: BOM Line Lifecycle — 17 states."""

    def test_bom_line_has_17_states(self):
        assert len(BOMLineStatus) == 17

    def test_pipeline_path(self):
        path = [
            BOMLineStatus.RAW,
            BOMLineStatus.NORMALIZING,
            BOMLineStatus.NORMALIZED,
            BOMLineStatus.ENRICHING,
            BOMLineStatus.ENRICHED,
            BOMLineStatus.SCORING,
            BOMLineStatus.SCORED,
        ]
        for i in range(len(path) - 1):
            assert (path[i], path[i + 1]) in BOM_LINE_TRANSITIONS

    def test_error_recovery(self):
        assert (BOMLineStatus.ERROR, BOMLineStatus.RAW) in BOM_LINE_TRANSITIONS
        assert (BOMLineStatus.ERROR, BOMLineStatus.NORMALIZING) in BOM_LINE_TRANSITIONS

    def test_needs_review_branch(self):
        assert (BOMLineStatus.NORMALIZING, BOMLineStatus.NEEDS_REVIEW) in BOM_LINE_TRANSITIONS
        assert (BOMLineStatus.NEEDS_REVIEW, BOMLineStatus.NORMALIZED) in BOM_LINE_TRANSITIONS


class TestEnums:
    def test_all_enums_json_serializable(self):
        import json
        for status in ProjectStatus:
            assert json.dumps(status) == f'"{status}"'
        for status in BOMLineStatus:
            assert json.dumps(status) == f'"{status}"'

    def test_rfq_status_values(self):
        assert len(RFQStatus) == 7

    def test_quote_status_values(self):
        assert len(QuoteStatus) == 9

    def test_po_status_values(self):
        assert len(POStatus) == 15

    def test_shipment_status_values(self):
        assert len(ShipmentStatus) == 9

    def test_invoice_status_values(self):
        assert len(InvoiceStatus) == 11

    def test_vendor_status_values(self):
        assert len(VendorStatus) == 8
