"""
admin.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Admin Endpoint Schema Layer

CONTRACT AUTHORITY: contract.md §4.13 (Admin Endpoints), §5.6 (Replay),
§2.28 (Consolidation_Insight), §2.74–§2.76 (Audit/Freshness/Integration logs),
§3.19 (SM-016 DataSubjectRequest), §6 (Ownership Rules).

Admin endpoints (Repo C; role: pgi_admin or admin):
  GET  /api/v1/admin/audit-log                  — paginated event audit log
  POST /api/v1/admin/data-subject-requests       — create DSR (schema in audit.py)
  POST /api/v1/admin/refresh/baseline-price      — trigger market data refresh
  POST /api/v1/admin/refresh/forex               — trigger forex refresh
  POST /api/v1/admin/refresh/tariff              — trigger tariff refresh
  POST /api/v1/admin/refresh/logistics           — trigger logistics refresh
  GET  /api/v1/admin/freshness-log               — paginated freshness log
  POST /api/v1/admin/normalization/replay        — trigger normalization replay
  POST /api/v1/projects/{id}/consolidation-analysis — consolidation analysis

Invariants:
  • All admin endpoints require role pgi_admin (GET audit/freshness) or
    admin (refresh triggers).  Enforced by Repo C RBAC middleware.
  • Refresh triggers enqueue async workers and return 202 Accepted.
    They do NOT block on completion.
  • NormalizationReplayRequest.scope: "project" | "organization" | "all".
    - "all" is restricted to pgi_admin only.
    - scope_id is required when scope is "project" or "organization".
  • Data-subject-request creation schema lives in audit.py to keep DSR
    schemas co-located with the rest of the compliance domain.
  • ConsolidationInsightSchema is imported from intelligence.py — admin.py
    provides only the response wrapper.
  • Market data refresh is always idempotent at the worker level; duplicate
    triggers are deduplicated in the task queue.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    EventActorType,
    CountryCode,
    CurrencyCode,
    FreshnessLogStatus,
    HSCode,
    NLPModelVersion,
    PGIBase,
    SignedMoney,
    VersionStr,
)

if TYPE_CHECKING:
    from .intelligence import ConsolidationInsightSchema


# ──────────────────────────────────────────────────────────────────────────
# Admin audit-log query parameters (GET /api/v1/admin/audit-log)
# ──────────────────────────────────────────────────────────────────────────

class AdminAuditLogQueryParams(PGIBase):
    """Query parameters for paginated admin audit log retrieval.

    All filters are optional and conjunctive (AND'd).
    Results are sorted by created_at DESC.
    limit: capped at 200 rows per request for performance.
    """

    entity_type: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Filter by entity type (e.g. 'bom_line', 'purchase_order').",
    )
    entity_id: Optional[UUID] = Field(
        default=None,
        description="Filter by specific entity UUID.",
    )
    user_id: Optional[UUID] = Field(
        default=None,
        description="Filter by acting user ID.",
    )
    actor_type: Optional[EventActorType] = Field(
        default=None,
        description="Filter by actor type: user | system | vendor | admin.",
    )
    event_type: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Filter by event type string (exact match or prefix).",
    )
    from_dt: Optional[datetime] = Field(
        default=None,
        description="Inclusive lower bound for created_at (UTC).",
    )
    to_dt: Optional[datetime] = Field(
        default=None,
        description="Exclusive upper bound for created_at (UTC).",
    )
    cursor: Optional[str] = Field(
        default=None,
        description="Opaque cursor from previous response for page continuation.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Number of records per page (max 200).",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "AdminAuditLogQueryParams":
        if self.from_dt and self.to_dt and self.from_dt >= self.to_dt:
            raise ValueError("from_dt must be strictly before to_dt.")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Admin freshness-log query parameters (GET /api/v1/admin/freshness-log)
# ──────────────────────────────────────────────────────────────────────────

class AdminFreshnessLogQueryParams(PGIBase):
    """Query parameters for the data freshness log admin endpoint.

    Results are sorted by fetched_at DESC.
    """

    table_name: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "Filter by source table name "
            "(e.g. 'baseline_price', 'forex_rate', 'tariff_rate')."
        ),
    )
    status: Optional[FreshnessLogStatus] = Field(
        default=None,
        description="Filter by refresh attempt status: success | error | stale.",
    )
    from_dt: Optional[datetime] = Field(
        default=None,
        description="Inclusive lower bound for fetched_at (UTC).",
    )
    to_dt: Optional[datetime] = Field(
        default=None,
        description="Exclusive upper bound for fetched_at (UTC).",
    )
    cursor: Optional[str] = Field(default=None)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_date_range(self) -> "AdminFreshnessLogQueryParams":
        if self.from_dt and self.to_dt and self.from_dt >= self.to_dt:
            raise ValueError("from_dt must be strictly before to_dt.")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Market data refresh triggers (POST /api/v1/admin/refresh/*)
# All return 202 Accepted — work is enqueued, not completed synchronously.
# ──────────────────────────────────────────────────────────────────────────

class RefreshBaselinePriceRequest(PGIBase):
    """Trigger a baseline price refresh for specific parts or commodity groups.

    POST /api/v1/admin/refresh/baseline-price

    Scope semantics:
    - Provide ``part_ids`` to refresh prices for specific Part_Master rows.
    - Provide ``commodity_groups`` to refresh by commodity group classification.
    - Omit both (or send null) to trigger a full platform-wide baseline price
      refresh (pgi_admin only).
    - Both may be provided to narrow the refresh to their intersection.
    """

    part_ids: Optional[list[UUID]] = Field(
        default=None,
        description="Specific Part_Master UUIDs to refresh baseline prices for.",
    )
    commodity_groups: Optional[list[str]] = Field(
        default=None,
        description="Commodity group labels to refresh (e.g. 'passive_components').",
    )

    @model_validator(mode="after")
    def validate_at_least_one_or_full_refresh(self) -> "RefreshBaselinePriceRequest":
        # Both null = full platform refresh; acceptable but pgi_admin only
        # (authorization enforced at route layer, not schema layer).
        if self.part_ids is not None and len(self.part_ids) == 0:
            raise ValueError("part_ids must be null (omit) or a non-empty list.")
        if self.commodity_groups is not None and len(self.commodity_groups) == 0:
            raise ValueError(
                "commodity_groups must be null (omit) or a non-empty list."
            )
        return self


class RefreshForexRequest(PGIBase):
    """Trigger a forex rate refresh for specific currency pairs.

    POST /api/v1/admin/refresh/forex

    ``currency_pairs``: list of [from_currency, to_currency] pairs, each a
    3-character ISO-4217 code.  Omit or pass null to refresh all tracked pairs.
    """

    currency_pairs: Optional[list[tuple[CurrencyCode, CurrencyCode]]] = Field(
        default=None,
        description=(
            "List of [from_currency, to_currency] pairs to refresh. "
            "Each element is a 2-element list of ISO-4217 currency codes."
        ),
    )

    @model_validator(mode="after")
    def validate_pairs_non_empty(self) -> "RefreshForexRequest":
        if self.currency_pairs is not None and len(self.currency_pairs) == 0:
            raise ValueError(
                "currency_pairs must be null (omit) or a non-empty list."
            )
        return self


class RefreshTariffRequest(PGIBase):
    """Trigger a tariff rate refresh for specific HS codes or country pairs.

    POST /api/v1/admin/refresh/tariff

    ``hs_codes``: list of HS codes (4–12 characters, §2.1).
    ``country_pairs``: list of [from_country, to_country] ISO-3166 alpha-2 pairs.
    Either, both, or neither (= full refresh) may be supplied.
    """

    hs_codes: Optional[list[HSCode]] = Field(
        default=None,
        description="HS codes to refresh tariff data for (4–12 character strings).",
    )
    country_pairs: Optional[list[tuple[CountryCode, CountryCode]]] = Field(
        default=None,
        description=(
            "List of [from_country, to_country] ISO-3166 alpha-2 pairs "
            "to scope the tariff refresh."
        ),
    )

    @model_validator(mode="after")
    def validate_non_empty_lists(self) -> "RefreshTariffRequest":
        if self.hs_codes is not None and len(self.hs_codes) == 0:
            raise ValueError("hs_codes must be null or a non-empty list.")
        if self.country_pairs is not None and len(self.country_pairs) == 0:
            raise ValueError("country_pairs must be null or a non-empty list.")
        return self


class RefreshLogisticsRequest(PGIBase):
    """Trigger a logistics rate refresh for specific origin-destination routes.

    POST /api/v1/admin/refresh/logistics

    ``route_keys``: opaque route identifiers in the form
    ``"{origin_country}:{destination_country}:{carrier}:{service_level}"``
    (e.g. ``"CN:US:DHL:EXPRESS"``).  Omit or pass null for a full refresh.
    """

    route_keys: Optional[list[str]] = Field(
        default=None,
        description=(
            "Route key strings in the format "
            "'{origin}:{destination}:{carrier}:{service_level}'. "
            "Omit or pass null to refresh all tracked routes."
        ),
    )

    @model_validator(mode="after")
    def validate_non_empty(self) -> "RefreshLogisticsRequest":
        if self.route_keys is not None and len(self.route_keys) == 0:
            raise ValueError("route_keys must be null or a non-empty list.")
        return self


class RefreshAcceptedResponse(PGIBase):
    """202 Accepted response body for all market data refresh triggers.

    The ``task_ids`` field lists the Celery task IDs enqueued so that
    the caller can optionally poll for completion via the admin task API.
    """

    accepted: bool = True
    task_ids: list[str] = Field(
        default_factory=list,
        description="Celery task IDs that were enqueued for this refresh.",
    )
    message: str = Field(
        default="Refresh job(s) enqueued.",
        description="Human-readable confirmation message.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Normalization replay (POST /api/v1/admin/normalization/replay)
# ──────────────────────────────────────────────────────────────────────────

class NormalizationReplayRequest(PGIBase):
    """Trigger a batch normalization replay on a new NLP model version.

    POST /api/v1/admin/normalization/replay

    Replay re-normalizes BOM lines using the specified ``target_nlp_model_version``
    and writes new Normalization_Trace rows.  Lines where ``significant_change``
    is True are flagged for buyer review (status → NEEDS_REVIEW).

    Scope rules (enforced by Repo C authorization middleware):
    - "project"       → scope_id required (project_id); accessible by buyer
                        who owns the project or pgi_admin / admin.
    - "organization"  → scope_id required (organization_id); admin or pgi_admin.
    - "all"           → scope_id must be null; pgi_admin only.

    Implementation: Repo C enqueues intelligence.run_normalization_replay
    Celery task (§8 file manifest — replay_worker.py).  Repo B /api/v1/replay
    is called per batch of BOM lines (§5.6).
    """

    scope: str = Field(
        description="Replay scope: 'project' | 'organization' | 'all'.",
    )
    scope_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Required when scope is 'project' or 'organization'. "
            "Must be null when scope is 'all'."
        ),
    )
    target_nlp_model_version: VersionStr = Field(
        description="NLP model version to replay against (must exist in config_version).",
    )

    @model_validator(mode="after")
    def validate_scope_and_id(self) -> "NormalizationReplayRequest":
        if self.scope not in ("project", "organization", "all"):
            raise ValueError(
                "scope must be one of: 'project', 'organization', 'all'."
            )
        if self.scope in ("project", "organization") and self.scope_id is None:
            raise ValueError(
                f"scope_id is required when scope is '{self.scope}'."
            )
        if self.scope == "all" and self.scope_id is not None:
            raise ValueError(
                "scope_id must be null when scope is 'all'."
            )
        return self


class NormalizationReplayResponse(PGIBase):
    """202 Accepted response for a normalization replay trigger.

    ``replay_run_id`` is the UUID of the Normalization_Run row created for
    this replay batch.  Callers can use it to poll replay progress.
    """

    replay_run_id: UUID = Field(
        description="Normalization_Run.run_id for the newly enqueued replay.",
    )
    scope: str
    scope_id: Optional[UUID] = None
    target_nlp_model_version: str
    enqueued_at: datetime = Field(
        description="Timestamp at which the replay task was enqueued (UTC).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Consolidation analysis
# (POST /api/v1/projects/{id}/consolidation-analysis)
#
# The request body is empty ({}).
# The response wraps ConsolidationInsightSchema objects from intelligence.py.
# We import the schema here to compose the response wrapper.
# ──────────────────────────────────────────────────────────────────────────

class ConsolidationAnalysisResponse(PGIBase):
    """Response for POST /api/v1/projects/{id}/consolidation-analysis.

    ``insights`` contains consolidation opportunities found by Repo B strategy
    logic, persisted by Repo C, and returned here.

    Each insight references a vendor and a set of BOM lines that can be
    consolidated to a single PO for that vendor (CN-16: covered_bom_line_ids
    is computed from consolidation_insight_line join table at serialization time).

    HTTP 200: analysis complete and insights persisted.
    HTTP 202: analysis job enqueued (returned if triggered async; insight list
              may be empty until job completes — check via project BOM-line endpoint).
    """

    project_id: UUID
    insights: list["ConsolidationInsightSchema"] = Field(
        default_factory=list,
        description="ConsolidationInsight objects for this project.",
    )
    total_estimated_savings_usd: Optional[SignedMoney] = Field(
        default=None,
        description="Sum of estimated_savings across all insights, in USD.",
    )
    analyzed_at: datetime = Field(
        description="Timestamp when the consolidation analysis was completed (UTC).",
    )


from .intelligence import ConsolidationInsightSchema as _ConsolidationInsightSchema

ConsolidationAnalysisResponse.model_rebuild(
    _types_namespace={"ConsolidationInsightSchema": _ConsolidationInsightSchema}
)
