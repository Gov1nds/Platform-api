"""
analytics.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Analytics & Reporting Schema Layer

CONTRACT AUTHORITY: contract.md §2.84–2.88 (ReportSchedule, ReportRun,
InsightSummary, analytics snapshots, SnapshotMetadata), §4.9 (Reports
endpoints), requirements.yaml domains/analytics_and_reporting.

Entities: ReportSchedule, ReportRun, InsightSummary, SpendSnapshot,
SavingsSnapshot, CategoryInsight, RiskDashboardSnapshot,
QuoteIntelligenceSnapshot, OperationalStatusView (materialized),
LeadTimeAnalysis, SnapshotMetadata.

Invariants:
  • Analytics reads from read-replica; never mutates source records.
  • Insight summaries are cached; never recomputed on each page load.
  • Operational_Status_View: materialized view refreshed every 5 minutes.
  • Report_Run.state: QUEUED → RUNNING → SUCCEEDED | FAILED.
  • All report endpoints require bearer + PRO or ENTERPRISE plan.
  • format query param: json | pdf | excel — PDF/Excel render on the fly.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import (
    DataFreshnessEnvelope,
    Money,
    PGIBase,
    ReportCadence,
    ReportRunState,
    SignedMoney,
    SpendGrouping,
)


# ──────────────────────────────────────────────────────────────────────────
# Report_Schedule (contract §2.84)
# ──────────────────────────────────────────────────────────────────────────

class ReportScheduleSchema(PGIBase):
    """Scheduled report configuration for an organization."""

    schedule_id: UUID
    organization_id: UUID
    report_type: str = Field(max_length=32)
    cadence: ReportCadence
    cron_expression: Optional[str] = Field(default=None, max_length=64)
    recipients_json: list[str] = Field(default_factory=list)
    created_at: datetime
    next_run_at: Optional[datetime] = None


class ReportScheduleCreateRequest(PGIBase):
    """POST /api/v1/reports/schedule."""

    report_type: str = Field(min_length=1, max_length=32)
    cadence: ReportCadence
    cron_expression: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Required when cadence='custom'.",
    )
    recipients_json: list[str] = Field(
        default_factory=list,
        description="Email addresses to deliver the report to.",
    )


class ReportScheduleCreateResponse(PGIBase):
    """Response after creating a report schedule."""

    schedule_id: UUID
    next_run_at: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────
# Report_Run (contract §2.85)
# ──────────────────────────────────────────────────────────────────────────

class ReportRunSchema(PGIBase):
    """A single execution of a report schedule.

    artifact_s3_url: presigned S3 URL to the rendered PDF or Excel file.
    """

    run_id: UUID
    schedule_id: Optional[UUID] = None
    organization_id: UUID
    report_type: str
    state: ReportRunState
    artifact_s3_url: Optional[str] = Field(default=None, max_length=1024)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Insight_Summary (contract §2.86)
# ──────────────────────────────────────────────────────────────────────────

class InsightSummarySchema(PGIBase):
    """AI-generated 3–5 sentence plain-language summary for a report section.

    Generated nightly by ETL; cached until next run.
    Surfaced as an InsightSummaryCard in the UI (LAW-2: explain everything).
    """

    summary_id: UUID
    report_run_id: UUID
    section: str = Field(max_length=64)
    summary_text: str
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Analytics Snapshot Entities (contract §2.87)
# ──────────────────────────────────────────────────────────────────────────

class SpendSnapshotSchema(PGIBase):
    """Aggregated spend by grouping dimension for a period.

    grouping: category | vendor | project | month | country.
    data_json: dimension-specific breakdown (e.g. category → spend amounts).
    """

    snapshot_id: UUID
    organization_id: UUID
    period_start: date = Field(description="ISO date YYYY-MM-DD.")
    period_end: date = Field(description="ISO date YYYY-MM-DD.")
    grouping: SpendGrouping
    data_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SavingsSnapshotSchema(PGIBase):
    """Realized savings vs baseline for a period.

    total_savings: sum of (baseline_price − negotiated_po_unit_price) × qty
    across all closed POs in the period.
    """

    snapshot_id: UUID
    organization_id: UUID
    period_start: date
    period_end: date
    total_savings: SignedMoney
    data_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CategoryInsightSchema(PGIBase):
    """Spend and sourcing trends for a specific category."""

    insight_id: UUID
    organization_id: UUID
    category: str = Field(max_length=128)
    data_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RiskDashboardSnapshotSchema(PGIBase):
    """Aggregated risk flag data across all active projects and orders."""

    snapshot_id: UUID
    organization_id: UUID
    data_json: dict[str, Any] = Field(
        description=(
            "Contains: risk_flag_counts by type and severity, "
            "at_risk_bom_lines, sole_source_parts, high_tariff_exposure_lines, etc."
        )
    )
    created_at: datetime


class QuoteIntelligenceSnapshotSchema(PGIBase):
    """Quote-to-order conversion and negotiation metrics for a period."""

    snapshot_id: UUID
    organization_id: UUID
    period_start: date
    period_end: date
    data_json: dict[str, Any] = Field(
        description=(
            "Contains: rfqs_sent, quotes_received, quotes_accepted, "
            "avg_quote_to_order_days, avg_tlc_savings_pct, etc."
        )
    )
    created_at: datetime


class OperationalStatusView(PGIBase):
    """Materialized view refreshed every 5 minutes — live operational dashboard.

    This is a snapshot of counts across active procurement entities.
    """

    organization_id: UUID
    active_orders: int
    open_rfqs: int
    pending_quotes: int
    orders_delayed: int
    pending_approvals: int


class LeadTimeAnalysisSchema(PGIBase):
    """Declared vs actual lead time analysis for a period."""

    analysis_id: UUID
    organization_id: UUID
    period_start: date
    period_end: date
    data_json: dict[str, Any] = Field(
        description=(
            "Contains: avg_declared_lead_time_weeks, avg_actual_lead_time_weeks, "
            "on_time_pct, vendor_breakdowns, category_breakdowns."
        )
    )
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Snapshot_Metadata (contract §2.88)
# ──────────────────────────────────────────────────────────────────────────

class SnapshotMetadataSchema(PGIBase):
    """Build metadata for a nightly snapshot rebuild."""

    metadata_id: UUID
    snapshot_table: str = Field(max_length=64)
    snapshot_date: date = Field(description="ISO date YYYY-MM-DD.")
    source_rows: int = Field(ge=0)
    built_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Report API responses (contract §4.9)
# ──────────────────────────────────────────────────────────────────────────

class SpendReportResponse(PGIBase):
    """GET /api/v1/reports/spend."""

    data: SpendSnapshotSchema
    insight_summary: Optional[str] = None
    export_url: Optional[str] = Field(
        default=None, description="Presigned URL to download PDF or Excel export."
    )

    data_freshness: DataFreshnessEnvelope


class SavingsReportResponse(PGIBase):
    """GET /api/v1/reports/savings."""

    data: SavingsSnapshotSchema
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class SupplierPerformanceReportResponse(PGIBase):
    """GET /api/v1/reports/supplier-performance."""

    data: list[dict[str, Any]] = Field(default_factory=list)
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class OperationalStatusReportResponse(PGIBase):
    """GET /api/v1/reports/operational-status."""

    data: OperationalStatusView
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class LeadTimeReportResponse(PGIBase):
    """GET /api/v1/reports/lead-time."""

    data: LeadTimeAnalysisSchema
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class RiskReportResponse(PGIBase):
    """GET /api/v1/reports/risk."""

    data: RiskDashboardSnapshotSchema
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class QuoteIntelligenceReportResponse(PGIBase):
    """GET /api/v1/reports/quote-intelligence."""

    data: QuoteIntelligenceSnapshotSchema
    insight_summary: Optional[str] = None

    data_freshness: DataFreshnessEnvelope


class CategoryInsightsReportResponse(PGIBase):
    """GET /api/v1/reports/category-insights."""

    data: list[CategoryInsightSchema]
    insight_summary: Optional[str] = None
    data_freshness: DataFreshnessEnvelope
