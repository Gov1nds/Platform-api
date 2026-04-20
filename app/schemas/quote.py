"""
quote.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Quote & Award Schema Layer

CONTRACT AUTHORITY: contract.md §2.39–2.43 (Quote, QuoteLine, QuoteRevision,
AwardDecision, ComparisonRun), §3.6 (SM-005 Quote.status), §4.6 (RFQ & Quote
endpoints).

CN-6: Quote status vocabulary is uppercase 9-state (SM-005).

Invariants:
  • forex_rate_at_submission and forex_rate_id_locked: LOCKED after SUBMITTED.
  • tariff_snapshot_id: locked at quote comparison run time.
  • valid_until must be in the future at submission (DRAFT → SUBMITTED).
  • UNIQUE constraint on quote: (rfq_id, vendor_id).
  • UNIQUE constraint on quote_line: (quote_id, bom_line_id).
  • UNIQUE constraint on award_decision: (rfq_id, bom_line_id).
  • UNIQUE constraint on quote_revision: (quote_id, revision_number).
  • Quote_Revision is APPEND-ONLY.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    SourcingMode,
    DataFreshnessEnvelope,
    CountryCode,
    CurrencyCode,
    IdempotencyKey,
    Money,
    OfferEventType,
    PGIBase,
    PositiveMoney,
    QuoteSourceChannel,
    QuoteStatus,
    SignedMoney,
    TIMESTAMPTZ,
)


# ──────────────────────────────────────────────────────────────────────────
# Quote (contract §2.39)
# ──────────────────────────────────────────────────────────────────────────

class QuoteResponse(PGIBase):
    """Full Quote entity.

    forex_rate_at_submission: LOCKED once status = SUBMITTED.
    forex_rate_id_locked:     FK to forex_rate row that is LOCKED for this quote.
    tariff_snapshot_id:       FK to tariff_rate row locked at comparison run time.
    tlc_calculated:           Total Landed Cost computed by Repo B at submission.
    """

    quote_id: UUID
    rfq_id: UUID
    vendor_id: UUID
    status: QuoteStatus
    quote_currency: CurrencyCode
    valid_until: datetime
    quote_date: datetime
    source_channel: QuoteSourceChannel
    total_quoted_value: SignedMoney
    forex_rate_at_submission: Optional[Decimal] = Field(
        default=None,
        description="LOCKED at SUBMITTED. DECIMAL(20, 8) serialized as Decimal.",
    )
    forex_rate_id_locked: Optional[UUID] = None
    tariff_snapshot_id: Optional[UUID] = None
    tlc_calculated: Optional[SignedMoney] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Expanded sub-resources
    lines: Optional[list["QuoteLineSchema"]] = None
    revisions: Optional[list["QuoteRevisionSchema"]] = None
    data_freshness: Optional[DataFreshnessEnvelope] = None


class BuyerFacingQuoteResponse(QuoteResponse):
    """Buyer-side quote projection for received quotes and quote comparison."""


class VendorFacingQuoteResponse(QuoteResponse):
    """Vendor-side quote projection; routers must scope it to the vendor's own quote."""


class QuoteListResponse(PGIBase):
    """All quotes received for an RFQ (GET /api/v1/rfqs/{id}/quotes)."""

    quotes: list[QuoteResponse]


# ──────────────────────────────────────────────────────────────────────────
# Quote_Line (contract §2.40)
# ──────────────────────────────────────────────────────────────────────────

class QuoteLineSchema(PGIBase):
    """A vendor's pricing and terms for a single BOM line.

    UNIQUE constraint: (quote_id, bom_line_id).
    tlc_unit and tlc_total are computed by Repo B using the TLC formula.
    """

    quote_line_id: UUID
    quote_id: UUID
    bom_line_id: UUID
    unit_price: PositiveMoney
    tooling_cost: Money = Field(default=Decimal("0"))
    moq: Decimal = Field(gt=Decimal("0"))
    lead_time_weeks: Decimal = Field(gt=Decimal("0"), decimal_places=2)
    payment_terms: Optional[str] = Field(default=None, max_length=64)
    shipping_terms: Optional[str] = Field(default=None, max_length=64)
    quality_docs_attached: list[Any] = Field(default_factory=list)
    tax_tariff_impact: Money = Field(default=Decimal("0"))
    freight_estimate: Money = Field(default=Decimal("0"))
    tlc_unit: Money
    tlc_total: SignedMoney
    substitute_proposed: Optional[UUID] = Field(
        default=None,
        description="FK part_master — vendor's proposed alternative part.",
    )
    notes: Optional[str] = None

    # Denormalized
    normalized_name: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Quote_Revision (contract §2.41)
# ──────────────────────────────────────────────────────────────────────────

class QuoteRevisionSchema(PGIBase):
    """An append-only record of a quote revision.

    Created when: (a) vendor submits a revision after REVISION_REQUESTED,
    or (b) a chat offer is accepted (offer_event_id populated).
    UNIQUE: (quote_id, revision_number) starting from 1.
    """

    revision_id: UUID
    quote_id: UUID
    revision_number: int = Field(ge=1)
    diff_json: dict[str, Any] = Field(
        description="JSON diff of changed fields between this revision and the prior."
    )
    submitted_at: datetime
    offer_event_id: Optional[UUID] = Field(
        default=None,
        description="Set when this revision originated from an accepted chat offer.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Vendor quote submission — POST /api/v1/vendor/quotes
# ──────────────────────────────────────────────────────────────────────────

class VendorQuoteLineRequest(PGIBase):
    """A single line in a vendor's quote submission."""

    bom_line_id: UUID
    unit_price: PositiveMoney
    tooling_cost: Money = Field(default=Decimal("0"))
    moq: Decimal = Field(gt=Decimal("0"), default=Decimal("1"))
    lead_time_weeks: Decimal = Field(gt=Decimal("0"))
    payment_terms: Optional[str] = Field(default=None, max_length=64)
    shipping_terms: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None


class VendorQuoteCreateRequest(PGIBase):
    """POST /api/v1/vendor/quotes — vendor submits a quote for an RFQ.

    Repo C locks forex_rate at submission and computes TLC via Repo B.
    Idempotency-Key header required.
    Errors: 422 after_deadline, 503 forex_provider_down.
    """

    rfq_id: UUID
    quote_currency: CurrencyCode
    valid_until: TIMESTAMPTZ = Field(description="Quote expiry — must be in the future.")
    lines: list[VendorQuoteLineRequest] = Field(min_length=1)
    quality_docs_attached: list[UUID] = Field(
        default_factory=list,
        description="Document vault UUIDs for quality/compliance docs.",
    )

    @model_validator(mode="after")
    def valid_until_future(self) -> "VendorQuoteCreateRequest":
        from datetime import timezone
        now = datetime.now(tz=timezone.utc)
        valid_until = self.valid_until
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if valid_until <= now:
            raise ValueError("valid_until must be in the future.")
        return self


class VendorQuoteCreateResponse(PGIBase):
    """Response after a vendor submits a quote.

    forex_rate_at_submission: the locked rate used for TLC computation.
    tariff_snapshot_id: locked tariff record for this quote.
    """

    quote_id: UUID
    status: QuoteStatus  # Always SUBMITTED
    forex_rate_at_submission: Decimal
    tariff_snapshot_id: Optional[UUID] = None
    tlc_calculated: SignedMoney
    data_freshness: Optional[DataFreshnessEnvelope] = Field(
        default=None,
        description="Freshness of forex rate at submission.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Quote accept / reject — POST /api/v1/quotes/{id}/accept|reject
# ──────────────────────────────────────────────────────────────────────────

class QuoteAcceptResponse(PGIBase):
    """Response after a buyer accepts a quote."""

    quote_id: UUID
    status: QuoteStatus  # Always ACCEPTED


class QuoteRejectRequest(PGIBase):
    """Optional rejection reason."""

    reason: Optional[str] = Field(default=None, max_length=1000)


class QuoteRejectResponse(PGIBase):
    """Response after a buyer rejects a quote."""

    quote_id: UUID
    status: QuoteStatus  # Always REJECTED


# ──────────────────────────────────────────────────────────────────────────
# Award_Decision (contract §2.42)
# ──────────────────────────────────────────────────────────────────────────

class AwardDecisionSchema(PGIBase):
    """Records the award of a specific BOM line to a vendor via a quote line.

    UNIQUE: (rfq_id, bom_line_id) — one award per line per RFQ.
    comparison_run_id: links to the ComparisonRun snapshot used to decide.
    """

    award_id: UUID
    rfq_id: UUID
    bom_line_id: UUID
    quote_line_id: UUID
    awarded_vendor_id: UUID
    awarded_vendor_name: Optional[str] = None
    awarded_at: datetime
    decided_by: UUID
    comparison_run_id: UUID


# ──────────────────────────────────────────────────────────────────────────
# Comparison_Run (contract §2.43)
# ──────────────────────────────────────────────────────────────────────────

class ComparisonRunSchema(PGIBase):
    """An immutable snapshot of the TLC comparison matrix taken at a point in time.

    snapshot_json: the full TLC matrix with forex locks, tariff locks, and
    freight locks applied — IMMUTABLE after creation.
    auto_select_applied: True if Repo C's auto-select-best-per-line ran.
    """

    run_id: UUID
    rfq_id: UUID
    snapshot_json: dict[str, Any] = Field(
        description=(
            "Immutable TLC matrix snapshot: all quote lines × all BOM lines, "
            "with forex_locks, tariff_locks, and freight_locks embedded."
        )
    )
    auto_select_applied: bool = False
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Quote comparison matrix (GET /api/v1/projects/{id}/quote-comparison)
# ──────────────────────────────────────────────────────────────────────────

class QuoteComparisonCell(PGIBase):
    """One cell in the TLC comparison matrix (vendor × BOM line)."""

    vendor_id: UUID
    vendor_name: str
    quote_line_id: Optional[UUID] = None
    unit_price: Optional[PositiveMoney] = None
    tlc_total: Optional[SignedMoney] = None
    lead_time_weeks: Optional[Decimal] = None
    moq: Optional[Decimal] = None
    forex_rate_locked: Optional[Decimal] = None
    tariff_rate_locked: Optional[Decimal] = None
    freight_estimate: Optional[Money] = None
    is_best: bool = False
    is_awarded: bool = False


class QuoteComparisonStrategyColumn(PGIBase):
    """Strategy recommendation context for a BOM line in the matrix."""

    bom_line_id: UUID
    normalized_name: str
    recommended_mode: Optional[SourcingMode] = None
    q_break: Optional[Decimal] = None


class ForexLockSummary(PGIBase):
    """Forex rate locked for this comparison run."""

    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Decimal
    locked_at: datetime


class TariffLockSummary(PGIBase):
    """Tariff rate locked for this comparison run."""

    hs_code: str
    from_country: CountryCode
    to_country: CountryCode
    duty_rate: Decimal
    locked_at: datetime


class QuoteComparisonResponse(PGIBase):
    """Full quote comparison matrix (GET /api/v1/projects/{id}/quote-comparison)."""

    matrix: list[list[QuoteComparisonCell]]
    strategy_column: list[QuoteComparisonStrategyColumn]
    forex_locks: list[ForexLockSummary] = Field(default_factory=list)
    tariff_locks: list[TariffLockSummary] = Field(default_factory=list)
    comparison_run_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    data_freshness: DataFreshnessEnvelope


# Forward reference resolution
QuoteResponse.model_rebuild()
