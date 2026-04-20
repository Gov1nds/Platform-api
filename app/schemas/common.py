"""
common.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Shared Enumerations, Base Types, and Utility Validators

SOURCE OF TRUTH: contract.md §3 (Status Vocabularies) + §2.1 (Naming/Type
Conventions) + requirements.yaml (all state machines SM-001 through SM-016).

Rules enforced here:
  • All primary keys are UUID.
  • All timestamps are TIMESTAMPTZ → Python datetime with timezone.
  • All monetary values are Decimal(20, 8).
  • All ISO-4217 currency codes are str(3).
  • StrEnum values match case exactly as persisted in the database (contract §3).
  • Status columns use VARCHAR + CHECK constraint (no pg ENUM TYPE).
  • Pydantic v2 model_config: from_attributes=True (orm_mode).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


# ──────────────────────────────────────────────────────────────────────────
# Base model
# ──────────────────────────────────────────────────────────────────────────

class PGIBase(BaseModel):
    """Root base model shared by every Pydantic schema in PGI Hub.

    Enables:
      • ORM-mode (from_attributes) so SQLAlchemy model instances can be
        passed directly to response serializers.
      • populate_by_name so both alias and field name are accepted.
      • Arbitrary types for Decimal, UUID, datetime.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )


# ──────────────────────────────────────────────────────────────────────────
# Annotated scalar types
# ──────────────────────────────────────────────────────────────────────────

# Monetary amount: DECIMAL(20, 8)
Money = Annotated[Decimal, Field(decimal_places=8, max_digits=20, ge=Decimal("0"))]

# Strictly positive monetary amount: DECIMAL(20, 8) CHECK > 0
PositiveMoney = Annotated[Decimal, Field(decimal_places=8, max_digits=20, gt=Decimal("0"))]

# Loose monetary — can be negative (e.g. savings delta)
SignedMoney = Annotated[Decimal, Field(decimal_places=8, max_digits=20)]

# Percentage as decimal 0..1
Ratio = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"), decimal_places=4)]

# Ratio with 3 decimal places (normalization confidence)
Confidence3 = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"), decimal_places=3)]

# Score out of 100 with 3 decimal places
Score100 = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"), decimal_places=3)]

# Score 0..100, 2 decimal places
Score100_2 = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"), decimal_places=2)]

# ISO-4217 currency code
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]

# ISO-3166 alpha-2 country code
CountryCode = Annotated[str, Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")]

# HS code (up to 12 chars per contract §2.1)
HSCode = Annotated[str, Field(min_length=4, max_length=12)]

# SHA-256 hex string
SHA256Hex = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]

# Idempotency key
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128)]

# Version string
VersionStr = Annotated[str, Field(min_length=1, max_length=32)]

# Weight profile hash (SHA-256 hex)
ProfileHash = Annotated[str, Field(min_length=64, max_length=64)]

# NLP model version
NLPModelVersion = Annotated[str, Field(min_length=1, max_length=32)]

# Scoring model version
ScoringModelVersion = Annotated[str, Field(min_length=1, max_length=32)]

# Correlation / trace IDs (W3C format or plain UUID)
CorrelationID = Annotated[str, Field(min_length=1, max_length=64)]

# TIMESTAMPTZ semantics for request payloads that accept datetimes.
TIMESTAMPTZ = Annotated[AwareDatetime, Field()]


# -----------------------------------------------------------------------------
# Canonical enum vocabulary
# -----------------------------------------------------------------------------
# The schemas layer intentionally imports status/type vocabularies from the
# models layer so validation, SQL CHECK constraints, and API contracts cannot
# drift independently.
from app.models.enums import (
    BOMLineStatus,
    ProjectState,
    ProjectSessionType,
    BOMUploadImportStatus,
    RFQStatus,
    QuoteStatus,
    PurchaseOrderStatus,
    ShipmentState,
    InvoiceState,
    VendorParticipation,
    VendorTier,
    VendorProfileClaimStatus,
    CertificationStatus,
    ApprovalRequestStatus,
    ChatThreadStatus,
    ReviewTaskStatus,
    FreshnessStatus,
    OutboxMessageState,
    DataSubjectRequestState,
    GuestSessionState,
    UserStatus,
    VendorUserStatus,
    OrganizationBillingPlan,
    UserRole,
    OrganizationMembershipRole,
    ChangeOrderStatus,
    DisputeStatus,
    ExceptionCaseStatus,
    TaskStatus,
    NotificationStatus,
    NormalizationRunStatus,
    ReportRunState,
    ShipmentEventSource,
    QuoteSourceChannel,
    RFQSendChannel,
    RFQInviteDeliveryMethod,
    NotificationChannel,
    BOMUploadSourceType,
    IntegrationCircuitState,
    VendorType,
    VendorPlatformIntegrationLevel,
    VendorCapabilityConfidenceSource,
    VendorScoreConfidence,
    VendorTierTransitionTrigger,
    ScoreDimension,
    SpendGrouping,
    BulkActionKind,
    VendorOrderUpdateType,
    PaymentMethod,
    GRLineCondition,
    InvoiceLineMatchStatus,
    ProjectWeightProfile,
    ProjectStage,
    Priority,
    ChatThreadType,
    ChatMessageType,
    ChatMessageSenderType,
    ChatMessageDeliveryStatus,
    OfferEventType,
    DocumentEntityType,
    DocumentVirusScanStatus,
    BaselinePriceSourceType,
    ForexRateSource,
    Carrier,
    CommodityExchange,
    CertificationType,
    EvidenceDataPointType,
    FeatureFlagScope,
    UserPrioritySensitivity,
    EventActorType,
    FreshnessLogStatus,
    ApprovalRequestEntityType,
    ApprovalDecisionValue,
    DisputeEntityType,
    TaskType,
    VendorFilterEliminationStep,
    SourcingMode,
    NormalizationDecisionType,
    PIIRedactionMethod,
    ExportControlClassification,
    RiskFlag,
    ExceptionType,
    Severity,
    ConfigType,
    DataSubjectRequestType,
    OAuthProvider,
    MFAMethod,
    ReportCadence,
    PaymentEventSource,
    GuestRateLimitScope,
    ProjectACLRole,
    VendorUserRole,
    WorkspaceDecisionStateValue,
    ThreadParticipantType,
    DataSourceLinkType,
    values_of,
)


# -----------------------------------------------------------------------------
# Shared sub-models (used across multiple schema domains)
# -----------------------------------------------------------------------------

class DeliveryLocation(PGIBase):
    """Geographic delivery location.  Used in BOM lines, projects, and
    intelligence-engine requests.
    """

    country: CountryCode
    state: Optional[str] = Field(default=None, max_length=128)
    city: Optional[str] = Field(default=None, max_length=255)
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)


class RiskFlagDetail(PGIBase):
    """A single risk flag with severity and mitigation guidance."""

    flag: RiskFlag
    severity: Severity
    mitigation: str


class WeightProfileValues(PGIBase):
    """Numeric weights for the 5 scoring dimensions.

    Each weight must be in [0, 1] and all five must sum to 1.0 (±0.001
    for floating-point tolerance).
    """

    cost: Annotated[float, Field(ge=0.0, le=1.0)]
    lead_time: Annotated[float, Field(ge=0.0, le=1.0)]
    quality: Annotated[float, Field(ge=0.0, le=1.0)]
    strategic: Annotated[float, Field(ge=0.0, le=1.0)]
    operational: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "WeightProfileValues":
        total = (
            self.cost
            + self.lead_time
            + self.quality
            + self.strategic
            + self.operational
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"Weight profile values must sum to 1.0; got {total:.4f}"
            )
        return self


class ErrorBody(PGIBase):
    """Standard API error response envelope (contract §4.0).

    All non-2xx responses from Repo C conform to this shape.
    """

    code: str = Field(description="Machine-readable error code.")
    message: str = Field(description="Human-readable error message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured context for the error.",
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description="Request correlation ID for distributed tracing.",
    )


class ErrorResponse(PGIBase):
    """Wrapper that matches the contract §4.0 envelope shape: { 'error': {...} }."""

    error: ErrorBody


ErrorEnvelope = ErrorBody


class PaginatedResponse(PGIBase):
    """Generic cursor-paginated response wrapper."""

    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Opaque cursor to pass as ?cursor= in the next request. "
            "Null when no further pages exist."
        ),
    )


class DataFreshnessEnvelope(PGIBase):
    """Contract-mandated freshness block for every estimate-producing response."""

    fetched_at: datetime
    freshness_status: FreshnessStatus
    stale_fields: list[str] = Field(default_factory=list)
    warning: Optional[str] = None


class FreshnessSummary(PGIBase):
    """Summary of data freshness included in intelligence responses (LAW-1)."""

    fetched_at: datetime
    freshness_status: FreshnessStatus
    source: str
    warning: Optional[str] = Field(
        default=None,
        description="Present when data is STALE — must be surfaced in the UI.",
    )

__all__ = [
    "PGIBase",
    "Money",
    "PositiveMoney",
    "SignedMoney",
    "Ratio",
    "Confidence3",
    "Score100",
    "Score100_2",
    "CurrencyCode",
    "CountryCode",
    "HSCode",
    "SHA256Hex",
    "IdempotencyKey",
    "VersionStr",
    "ProfileHash",
    "NLPModelVersion",
    "ScoringModelVersion",
    "CorrelationID",
    "TIMESTAMPTZ",
    "BOMLineStatus",
    "ProjectState",
    "ProjectSessionType",
    "BOMUploadImportStatus",
    "RFQStatus",
    "QuoteStatus",
    "PurchaseOrderStatus",
    "ShipmentState",
    "InvoiceState",
    "VendorParticipation",
    "VendorTier",
    "VendorProfileClaimStatus",
    "CertificationStatus",
    "ApprovalRequestStatus",
    "ChatThreadStatus",
    "ReviewTaskStatus",
    "FreshnessStatus",
    "OutboxMessageState",
    "DataSubjectRequestState",
    "GuestSessionState",
    "UserStatus",
    "VendorUserStatus",
    "OrganizationBillingPlan",
    "UserRole",
    "OrganizationMembershipRole",
    "ChangeOrderStatus",
    "DisputeStatus",
    "ExceptionCaseStatus",
    "TaskStatus",
    "NotificationStatus",
    "NormalizationRunStatus",
    "ReportRunState",
    "ShipmentEventSource",
    "QuoteSourceChannel",
    "RFQSendChannel",
    "RFQInviteDeliveryMethod",
    "NotificationChannel",
    "BOMUploadSourceType",
    "IntegrationCircuitState",
    "VendorType",
    "VendorPlatformIntegrationLevel",
    "VendorCapabilityConfidenceSource",
    "VendorScoreConfidence",
    "VendorTierTransitionTrigger",
    "ScoreDimension",
    "SpendGrouping",
    "BulkActionKind",
    "VendorOrderUpdateType",
    "PaymentMethod",
    "GRLineCondition",
    "InvoiceLineMatchStatus",
    "ProjectWeightProfile",
    "ProjectStage",
    "Priority",
    "ChatThreadType",
    "ChatMessageType",
    "ChatMessageSenderType",
    "ChatMessageDeliveryStatus",
    "OfferEventType",
    "DocumentEntityType",
    "DocumentVirusScanStatus",
    "BaselinePriceSourceType",
    "ForexRateSource",
    "Carrier",
    "CommodityExchange",
    "CertificationType",
    "EvidenceDataPointType",
    "FeatureFlagScope",
    "UserPrioritySensitivity",
    "EventActorType",
    "FreshnessLogStatus",
    "ApprovalRequestEntityType",
    "ApprovalDecisionValue",
    "DisputeEntityType",
    "TaskType",
    "VendorFilterEliminationStep",
    "SourcingMode",
    "NormalizationDecisionType",
    "PIIRedactionMethod",
    "ExportControlClassification",
    "RiskFlag",
    "ExceptionType",
    "Severity",
    "ConfigType",
    "DataSubjectRequestType",
    "OAuthProvider",
    "MFAMethod",
    "ReportCadence",
    "PaymentEventSource",
    "GuestRateLimitScope",
    "ProjectACLRole",
    "VendorUserRole",
    "WorkspaceDecisionStateValue",
    "ThreadParticipantType",
    "DataSourceLinkType",
    "DeliveryLocation",
    "RiskFlagDetail",
    "WeightProfileValues",
    "ErrorBody",
    "ErrorEnvelope",
    "ErrorResponse",
    "PaginatedResponse",
    "DataFreshnessEnvelope",
    "FreshnessSummary",
]
