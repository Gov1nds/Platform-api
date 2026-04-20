"""
guest.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Guest Lead Generation Schema Layer

CONTRACT AUTHORITY: contract.md §2.70–2.73 (Guest_Session, Guest_Search_Log,
Guest_Report_Snapshot, Guest_Rate_Limit_Bucket), §3.20 (SM-003 Guest_Session
states), §4.2 (Guest Endpoints), CN-14.

CN-14 dual-window rule:
  • Guest session cookie TTL: 30 days sliding (re-engagement identity).
  • Guest_Search_Log retention: 90 days before anonymization/hard-delete.

Invariants:
  • Guest reports use the SAME normalize/enrich/score pipeline as authenticated
    users — no separate code path (requirements.yaml/guest_and_lead_generation).
  • Guest session data is NEVER duplicated into the User table — MERGED on conversion.
  • vendor_results_json in GuestSearchLog is REDACTED (no contact info).
  • Rate limiting: per-IP and per-session (Redis primary; DB fallback table).
  • Free report caps at 5 components; top 3 vendors per component (redacted).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    FreshnessStatus,
    DataFreshnessEnvelope,
    CountryCode,
    CurrencyCode,
    GuestRateLimitScope,
    GuestSessionState,
    PGIBase,
    RiskFlagDetail,
)


# ──────────────────────────────────────────────────────────────────────────
# Guest_Session (contract §2.70)
# ──────────────────────────────────────────────────────────────────────────

class GuestSessionSchema(PGIBase):
    """An anonymous visitor's session.

    session_token: httpOnly cookie value — never exposed in API response body.
    state: NEW | ACTIVE | EXPIRED | CONVERTED (SM-003).
    Sliding cookie TTL: 30 days from last_active_at (CN-14).
    Search log retention: 90 days (CN-14).
    """

    session_id: UUID
    state: GuestSessionState
    detected_location_json: dict[str, Any] = Field(default_factory=dict)
    detected_currency: Optional[CurrencyCode] = None
    overridden_location_json: Optional[dict[str, Any]] = None
    overridden_currency: Optional[CurrencyCode] = None
    component_count: int = 0
    converted_to_user_id: Optional[UUID] = None
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime

    # session_token intentionally excluded from response schema
    # (set via httpOnly cookie by Repo C; never in response body)


# ──────────────────────────────────────────────────────────────────────────
# Guest_Search_Log (contract §2.71)
# ──────────────────────────────────────────────────────────────────────────

class GuestSearchLogSchema(PGIBase):
    """A recorded guest search event.

    vendor_results_json: REDACTED — no vendor contact information stored.
    Anonymized/deleted after 90 days (CN-14).
    """

    search_id: UUID
    session_id: UUID
    search_query: str
    components_json: list[Any] = Field(default_factory=list)
    detected_location_json: dict[str, Any] = Field(default_factory=dict)
    detected_currency: Optional[CurrencyCode] = None
    vendor_results_json: list[Any] = Field(
        default_factory=list,
        description="Redacted vendor match results — no contact info.",
    )
    free_report_generated: bool = False
    converted_to_signup: bool = False
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Guest_Report_Snapshot (contract §2.72)
# ──────────────────────────────────────────────────────────────────────────

class GuestReportSnapshotSchema(PGIBase):
    """A cached snapshot of a generated free intelligence report."""

    snapshot_id: UUID
    search_id: UUID
    report_json: dict[str, Any] = Field(
        description="Full free report JSON (see GuestIntelligenceReportResponse)."
    )
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Guest_Rate_Limit_Bucket (contract §2.73)
# ──────────────────────────────────────────────────────────────────────────

class GuestRateLimitBucketSchema(PGIBase):
    """Rate limit tracking bucket (DB fallback; primary is Redis).

    scope: 'ip' | 'session'.
    window_start / window_end: sliding window boundaries.
    UNIQUE: (scope, identifier, window_start).
    """

    bucket_id: UUID
    scope: GuestRateLimitScope
    identifier: str = Field(max_length=128)
    count: int = 0
    window_start: datetime
    window_end: datetime


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/guest/intelligence-report
# ──────────────────────────────────────────────────────────────────────────

class GuestComponentRequest(PGIBase):
    """A single component specification in a guest search request."""

    raw_text: str = Field(
        min_length=1,
        max_length=2000,
        description="Free-text component description.",
    )
    quantity: float = Field(gt=0)
    unit: Optional[str] = Field(default=None, max_length=32)


class GuestDeliveryLocationRequest(PGIBase):
    """Delivery location provided in the guest search (IP-detected or user-entered)."""

    country: CountryCode
    city: Optional[str] = Field(default=None, max_length=128)
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)


class GuestIntelligenceReportRequest(PGIBase):
    """POST /api/v1/guest/intelligence-report.

    Max 5 components (422 if exceeded).
    delivery_location and currency default to IP-detected values if omitted.
    No auth required; guest_session_token cookie optional.
    """

    components: list[GuestComponentRequest] = Field(
        min_length=1,
        max_length=5,
        description="Up to 5 components. 422 if more than 5 submitted.",
    )
    delivery_location: GuestDeliveryLocationRequest
    currency: CurrencyCode = Field(default="USD")


class CostEstimateBand(PGIBase):
    """Price band shown in the free report (LAW-1: freshness timestamp included)."""

    floor: float
    ceiling: float
    currency: CurrencyCode
    fetched_at: datetime
    label: str = Field(
        default="",
        description=(
            "ESTIMATED or BENCHMARK when actual data unavailable (LAW-1). "
            "Empty string when live data is available."
        ),
    )


class RedactedVendorMatch(PGIBase):
    """Vendor match shown in the free report with contact details REDACTED.

    Contact, exact prices, and score decomposition are locked features
    (hidden until sign-in per requirements/free_report_generator).
    """

    vendor_id: UUID
    name: str
    country: CountryCode
    score: float
    why_match: str = Field(
        description="Plain-language match rationale (LAW-2: explain everything)."
    )
    # Contact information intentionally omitted (locked feature)


class FreshnessSummaryItem(PGIBase):
    """Per-data-source freshness information surfaced in the free report."""

    source: str
    fetched_at: datetime
    freshness_status: FreshnessStatus
    warning: Optional[str] = None


class GuestReportComponent(PGIBase):
    """Intelligence result for a single component in the free report."""

    normalized_name: str
    category: str
    strategy: str = Field(description="Plain-language sourcing strategy recommendation.")
    cost_estimate: CostEstimateBand
    top_vendors: list[RedactedVendorMatch] = Field(
        max_length=3,
        description="Top 3 vendor matches — contact details redacted.",
    )
    risk_flags: list[RiskFlagDetail] = Field(default_factory=list)

    # Locked features (shown as teasers only)
    locked_features_teaser: list[str] = Field(
        default_factory=list,
        description=(
            "Labels of features unlocked after sign-in: "
            "'full_vendor_contact', 'exact_pricing', 'send_rfq', 'score_decomposition'."
        ),
    )


class GuestIntelligenceReportResponse(PGIBase):
    """Full response for POST /api/v1/guest/intelligence-report.

    Sets guest_session_token httpOnly cookie.
    Freshness timestamps attached to every price (LAW-1).
    """

    session_id: UUID
    report: "GuestReport"
    data_freshness: DataFreshnessEnvelope


class GuestReport(PGIBase):
    """The free intelligence report payload."""

    components: list[GuestReportComponent]
    generated_at: datetime
    freshness_summary: list[FreshnessSummaryItem] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/guest/detect-location
# ──────────────────────────────────────────────────────────────────────────

class DetectLocationRequest(PGIBase):
    """IP geolocation request — null IP uses the request's source IP."""

    ip: Optional[str] = Field(default=None, description="IPv4 or IPv6 address to look up.")


class DetectLocationResponse(PGIBase):
    """IP geolocation result (MaxMind / ipapi.co)."""

    country: CountryCode
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    currency: CurrencyCode


# ──────────────────────────────────────────────────────────────────────────
# GET /api/v1/guest/search/{session_id}
# ──────────────────────────────────────────────────────────────────────────

class GuestSearchHistoryResponse(PGIBase):
    """Return guest search history for re-engagement display."""

    session: GuestSessionSchema
    searches: list[GuestSearchLogSchema]
    reports: list[GuestReportSnapshotSchema]


# Forward reference resolution
GuestIntelligenceReportResponse.model_rebuild()
