"""
Analytics and reporting persistence models.

Contract anchors
----------------
§2.84  Report_Schedule             — scheduled report configuration
§2.85  Report_Run                  — one execution of a report
§2.86  Insight_Summary             — AI-generated section text, cached per run
§2.87  Analytics Snapshot Entities — spend, savings, category, risk, quote-intel,
                                     lead-time, operational-status
§2.88  Snapshot_Metadata           — ETL build provenance per snapshot table per date

Design invariants
-----------------
* All snapshot tables are WRITE-ONCE per ETL run — the nightly job inserts a
  new row; old rows are never updated.  Trend charts read multiple rows ordered
  by period_start.
* ``operational_status_view`` is modelled as a plain Python class (not a mapped
  SA table) because its backing DDL is a PostgreSQL MATERIALIZED VIEW refreshed
  every 5 minutes by a Celery beat task (contract §2.87).  Alembic will manage
  its CREATE MATERIALIZED VIEW statement via a raw-SQL migration; this file only
  provides the Python surface for service-layer access.
* ``report_run.schedule_id`` is nullable — ad-hoc runs (API-triggered) have no
  schedule.
* ``insight_summary`` rows are invalidated when their parent ``report_run``
  completes (ON DELETE CASCADE on ``report_run_id``).

Domain
------
analytics_and_reporting (§1.3 domain block)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CreatedAtMixin,
    enum_check,
    jsonb_array,
    jsonb_object,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
    uuid_polymorphic,
)
from app.models.enums import (
    ReportCadence,
    ReportRunState,
    SpendGrouping,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# ReportSchedule  (§2.84)
# ─────────────────────────────────────────────────────────────────────────────


class ReportSchedule(Base, CreatedAtMixin):
    """Configuration record for a recurring report delivery.

    One organization may have multiple schedules (e.g. weekly spend + monthly
    category insight).  ``cron_expression`` is populated only when
    ``cadence = 'custom'``.

    cadence CHECK: contract §3.84 ('weekly' | 'monthly' | 'custom').
    """

    __tablename__ = "report_schedule"

    # ── Primary key ──────────────────────────────────────────────────────────
    schedule_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Ownership ─────────────────────────────────────────────────────────────
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="CASCADE", index=True
    )

    # ── Configuration ─────────────────────────────────────────────────────────
    # e.g. "spend", "savings", "supplier_performance", "category_insights"
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    # null unless cadence='custom'
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON array of email addresses / user_ids to notify on delivery
    recipients_json: Mapped[list] = jsonb_array()

    # created_at inherited
    next_run_at: Mapped[datetime | None] = tstz(nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    runs: Mapped[list["ReportRun"]] = relationship(
        "ReportRun",
        back_populates="schedule",
        foreign_keys="ReportRun.schedule_id",
        lazy="raise",
    )

    __table_args__ = (
        enum_check("cadence", values_of(ReportCadence)),
        Index("ix_report_schedule_org_next_run", "organization_id", "next_run_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ReportRun  (§2.85)
# ─────────────────────────────────────────────────────────────────────────────


class ReportRun(Base, CreatedAtMixin):
    """One execution of a report — scheduled or ad-hoc.

    State machine: QUEUED → RUNNING → SUCCEEDED | FAILED  (contract §3.31).
    On SUCCEEDED, ``artifact_s3_url`` is populated with the S3 pre-signed URL
    (or permanent S3 key) of the rendered PDF / Excel file.

    ``schedule_id`` is nullable for ad-hoc API-triggered runs.
    """

    __tablename__ = "report_run"

    # ── Primary key ──────────────────────────────────────────────────────────
    run_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Ownership ─────────────────────────────────────────────────────────────
    schedule_id: Mapped[uuid.UUID | None] = uuid_fk(
        "report_schedule.schedule_id", ondelete="SET NULL", nullable=True, index=True
    )
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )

    # ── Parameters ────────────────────────────────────────────────────────────
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # ── State ─────────────────────────────────────────────────────────────────
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'QUEUED'")
    )

    # ── Artifact ──────────────────────────────────────────────────────────────
    artifact_s3_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    started_at: Mapped[datetime | None] = tstz(nullable=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    schedule: Mapped["ReportSchedule | None"] = relationship(
        "ReportSchedule",
        back_populates="runs",
        foreign_keys=[schedule_id],
        lazy="raise",
    )
    insight_summaries: Mapped[list["InsightSummary"]] = relationship(
        "InsightSummary",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        enum_check("state", values_of(ReportRunState)),
        Index("ix_report_run_org_state", "organization_id", "state"),
        Index("ix_report_run_state", "state"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# InsightSummary  (§2.86)
# ─────────────────────────────────────────────────────────────────────────────


class InsightSummary(Base, CreatedAtMixin):
    """AI-generated natural-language summary for one section of a report run.

    Generated by ``analytics_service.py`` using Jinja2 templates + live data
    substitution (contract §1.3 analytics domain — 'insight summary
    generation').  Cached on the run row so re-renders are instant.

    ``section`` examples: 'spend_overview', 'top_savings_opportunities',
    'supplier_risk', 'lead_time_trends'.
    """

    __tablename__ = "insight_summary"

    # ── Primary key ──────────────────────────────────────────────────────────
    summary_id: Mapped[uuid.UUID] = uuid_pk()

    # ── Parent ────────────────────────────────────────────────────────────────
    report_run_id: Mapped[uuid.UUID] = uuid_fk(
        "report_run.run_id", ondelete="CASCADE", index=True
    )

    # ── Content ───────────────────────────────────────────────────────────────
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)

    # created_at inherited

    # ── Relationships ─────────────────────────────────────────────────────────
    run: Mapped["ReportRun"] = relationship(
        "ReportRun",
        back_populates="insight_summaries",
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_insight_summary_run_section", "report_run_id", "section"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Analytics Snapshot tables  (§2.87)
# ─────────────────────────────────────────────────────────────────────────────


class SpendSnapshot(Base, CreatedAtMixin):
    """Aggregated spend for one (org, period, grouping) combination.

    grouping CHECK: 'category' | 'vendor' | 'project' | 'month' | 'country'.

    Written once by the nightly ETL; never updated.  The service layer reads
    the most-recent snapshot row for each (organization_id, period) pair.
    """

    __tablename__ = "spend_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    grouping: Mapped[str] = mapped_column(String(32), nullable=False)
    # Flexible shape: { groups: [{key, total_usd, item_count, ...}], currency, ... }
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        enum_check("grouping", values_of(SpendGrouping)),
        Index(
            "ix_spend_snapshot_org_period",
            "organization_id",
            "period_start",
            "period_end",
        ),
    )


class SavingsSnapshot(Base, CreatedAtMixin):
    """Aggregated savings vs baseline for one (org, period) combination.

    ``total_savings`` is in the organisation's default currency (DECIMAL(20,8)).
    ``data_json`` carries the per-category / per-vendor breakdown.
    """

    __tablename__ = "savings_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total_savings: Mapped[Decimal] = money_default_zero()
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index(
            "ix_savings_snapshot_org_period",
            "organization_id",
            "period_start",
            "period_end",
        ),
    )


class CategoryInsight(Base, CreatedAtMixin):
    """Commodity-category intelligence snapshot for an organisation.

    Generated nightly.  ``data_json`` holds: risk_score, top_vendors,
    average_lead_time, spend_trend, concentration_index.
    """

    __tablename__ = "category_insight"

    insight_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index("ix_category_insight_org_category", "organization_id", "category"),
    )


class RiskDashboardSnapshot(Base, CreatedAtMixin):
    """Point-in-time risk dashboard for an organisation.

    ``data_json`` carries: open_disputes, delayed_shipments, high_tariff_lines,
    sole_source_lines, stale_quotes, compliance_gaps.  Refreshed nightly.
    """

    __tablename__ = "risk_dashboard_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index("ix_risk_dashboard_snapshot_org", "organization_id", "created_at"),
    )


class QuoteIntelligenceSnapshot(Base, CreatedAtMixin):
    """Quote-to-order conversion intelligence for an (org, period).

    ``data_json`` holds: rfq_count, quote_count, acceptance_rate, avg_response_time_hrs,
    avg_revision_rounds, top_vendors_by_acceptance.
    """

    __tablename__ = "quote_intelligence_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index(
            "ix_quote_intel_snapshot_org_period",
            "organization_id",
            "period_start",
            "period_end",
        ),
    )


class LeadTimeAnalysis(Base, CreatedAtMixin):
    """Actual vs contracted lead-time analysis for an (org, period).

    ``data_json`` holds: avg_lead_time_days, p50_lead_time_days,
    p95_lead_time_days, on_time_delivery_rate, top_violating_vendors.
    """

    __tablename__ = "lead_time_analysis"

    analysis_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_json: Mapped[dict] = jsonb_object()

    __table_args__ = (
        Index(
            "ix_lead_time_analysis_org_period",
            "organization_id",
            "period_start",
            "period_end",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OperationalStatusView  (§2.87 — MATERIALIZED VIEW)
# ─────────────────────────────────────────────────────────────────────────────


class OperationalStatusView(Base):
    """Mapped SA class for the ``operational_status_view`` MATERIALIZED VIEW.

    The DDL is managed by a raw-SQL Alembic migration, NOT by SA metadata.
    This class exists solely to allow type-safe ORM reads in service code.

    The view is refreshed every 5 minutes via:
        REFRESH MATERIALIZED VIEW CONCURRENTLY operational_status_view;

    Columns match contract §2.87.  ``organization_id`` is the natural PK of the
    view (one row per org).
    """

    __tablename__ = "operational_status_view"

    organization_id: Mapped[uuid.UUID] = uuid_polymorphic(primary_key=True)
    active_orders: Mapped[int] = mapped_column(Integer, nullable=False)
    open_rfqs: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_quotes: Mapped[int] = mapped_column(Integer, nullable=False)
    orders_delayed: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_approvals: Mapped[int] = mapped_column(Integer, nullable=False)

    # Prevent Alembic from treating this as a regular table. Alembic env.py
    # must exclude table objects where info["is_view"] is true.
    __table_args__ = {
        "info": {"is_view": True},
        "extend_existing": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotMetadata  (§2.88)
# ─────────────────────────────────────────────────────────────────────────────


class SnapshotMetadata(Base, CreatedAtMixin):
    """ETL provenance record for every snapshot-table rebuild.

    Written by the nightly ETL pipeline once per (snapshot_table, snapshot_date)
    pair.  Used by monitoring to detect missed runs and by data-freshness
    dashboards to show when each analytic table was last populated.
    """

    __tablename__ = "snapshot_metadata"

    metadata_id: Mapped[uuid.UUID] = uuid_pk()
    snapshot_table: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = tstz(default_now=True)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_table",
            "snapshot_date",
            name="uq_snapshot_metadata_table_date",
        ),
        Index("ix_snapshot_metadata_table_date", "snapshot_table", "snapshot_date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "ReportSchedule",
    "ReportRun",
    "InsightSummary",
    "SpendSnapshot",
    "SavingsSnapshot",
    "CategoryInsight",
    "RiskDashboardSnapshot",
    "QuoteIntelligenceSnapshot",
    "LeadTimeAnalysis",
    "OperationalStatusView",
    "SnapshotMetadata",
]
