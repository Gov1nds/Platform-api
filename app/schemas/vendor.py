"""
vendor.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Vendor Marketplace Schema Layer

CONTRACT AUTHORITY: contract.md §2.13–2.24 + §4.5 (Vendor endpoints) +
requirements.yaml domains/marketplace_vendor_network.

Entities:
  Vendor, VendorUser, Vendor_Part_Capability, Vendor_Performance_Snapshot,
  Certification, Vendor_Profile_Claim, Vendor_Invite, Vendor_Tier_Transition,
  Vendor_Rating, Vendor_Filter_Result, Preferred_Vendor_List,
  Preferred_Vendor_Member.

Invariants:
  • vendor.certifications (JSONB) is denormalized cache; authoritative data
    lives in the certification table (CN-20 pattern).
  • VendorUser email is UNIQUE per vendor (not globally).
  • Vendor_Part_Capability: at least one of (part_id, commodity_group) non-null.
  • GHOST vendors cannot respond to RFQs (SM-009).
  • SUSPENDED vendors excluded from active sourcing but existing POs continue.
  • DEACTIVATED is permanent soft-delete; all historical data retained.
  • CN-18: vendor.participation (workflow state) and vendor.tier (buyer-facing
    classification) are SEPARATE fields.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .common import (
    VendorCapabilityConfidenceSource,
    CertificationStatus,
    CertificationType,
    CountryCode,
    CurrencyCode,
    Money,
    PGIBase,
    Score100_2,
    VendorFilterEliminationStep,
    VendorScoreConfidence,
    VendorParticipation,
    VendorPlatformIntegrationLevel,
    VendorProfileClaimStatus,
    VendorTier,
    VendorTierTransitionTrigger,
    VendorType,
    VendorUserRole,
    VendorUserStatus,
)


# ──────────────────────────────────────────────────────────────────────────
# Vendor (contract §2.13)
# ──────────────────────────────────────────────────────────────────────────

class VendorResponse(PGIBase):
    """Full Vendor entity.

    certifications field is DENORMALIZED CACHE ONLY — authoritative rows
    are in the certification table.
    """

    vendor_id: UUID
    name: str
    legal_entity: Optional[str] = None
    registration_number: Optional[str] = None
    vendor_type: VendorType
    country_of_origin: CountryCode
    regions_served: list[Any] = Field(default_factory=list)
    commodity_groups: list[str] = Field(default_factory=list)
    certifications: list[Any] = Field(
        default_factory=list,
        description="Denormalized cache. Authoritative data in certification table.",
    )
    capacity_profile: dict[str, Any] = Field(default_factory=dict)
    min_order_value: Money
    moq_by_category: dict[str, Any] = Field(default_factory=dict)
    lead_time_profile: dict[str, Any] = Field(default_factory=dict)
    quality_score: Optional[Score100_2] = None
    reliability_score: Optional[Score100_2] = None
    response_speed: Optional[Decimal] = Field(
        default=None, description="Average response speed in seconds."
    )
    payment_terms: dict[str, Any] = Field(default_factory=dict)
    shipping_terms: dict[str, Any] = Field(default_factory=dict)
    tax_profile: dict[str, Any] = Field(default_factory=dict)
    currency_support: list[str] = Field(default_factory=list)
    substitute_willingness: bool = False
    engineering_support: bool = False
    ships_on_their_own: bool = False
    active_status: bool = True
    profile_claimed_by: Optional[UUID] = None
    profile_completion_pct: int = Field(default=0, ge=0, le=100)
    verified_badge: bool = False
    participation: VendorParticipation
    tier: VendorTier
    platform_integration_level: VendorPlatformIntegrationLevel
    chat_response_avg_seconds: Optional[int] = None
    last_active_on_platform: Optional[datetime] = None
    onboarded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class BuyerFacingVendorResponse(PGIBase):
    """Buyer-safe vendor projection.

    Excludes vendor cost-structure and private commercial profile fields such as
    capacity_profile, moq_by_category, payment_terms, shipping_terms, tax_profile,
    and internal denormalized commercial details.
    """

    vendor_id: UUID
    name: str
    vendor_type: VendorType
    country_of_origin: CountryCode
    regions_served: list[Any] = Field(default_factory=list)
    commodity_groups: list[str] = Field(default_factory=list)
    certifications: list[Any] = Field(default_factory=list)
    quality_score: Optional[Score100_2] = None
    reliability_score: Optional[Score100_2] = None
    response_speed: Optional[Decimal] = None
    currency_support: list[str] = Field(default_factory=list)
    substitute_willingness: bool = False
    engineering_support: bool = False
    ships_on_their_own: bool = False
    profile_completion_pct: int = Field(default=0, ge=0, le=100)
    verified_badge: bool = False
    tier: VendorTier
    platform_integration_level: VendorPlatformIntegrationLevel
    last_active_on_platform: Optional[datetime] = None
    is_sourcing_eligible: bool = Field(
        default=False,
        description=(
            "True when the vendor can receive buyer sourcing workflows. "
            "Derived from internal participation state without exposing it."
        ),
    )


class VendorFacingVendorResponse(VendorResponse):
    """Full self-profile projection for vendor admins and vendor reps."""


class VendorSummaryResponse(PGIBase):
    """Compact Vendor for list views and shortlists."""

    vendor_id: UUID
    name: str
    vendor_type: VendorType
    country_of_origin: CountryCode
    commodity_groups: list[str]
    tier: VendorTier
    participation: VendorParticipation
    profile_completion_pct: int
    verified_badge: bool
    quality_score: Optional[Score100_2] = None
    ships_on_their_own: bool


class VendorDetailResponse(PGIBase):
    """Expanded GET /api/v1/vendors/{id} — includes related data sections."""

    vendor: VendorResponse
    capabilities: list["VendorPartCapabilitySchema"] = Field(default_factory=list)
    performance: Optional["VendorPerformanceSnapshotSchema"] = None
    certifications: list["CertificationSchema"] = Field(default_factory=list)
    commercial_terms: dict[str, Any] = Field(default_factory=dict)
    transaction_history_summary: dict[str, Any] = Field(default_factory=dict)


class VendorListResponse(PGIBase):
    """Cursor-paginated list of vendors."""

    items: list[VendorSummaryResponse]
    next_cursor: Optional[str] = None


class VendorUpdateRequest(PGIBase):
    """Partial vendor profile update — vendor-side PATCH /api/v1/vendor/profile."""

    legal_entity: Optional[str] = Field(default=None, max_length=255)
    regions_served: Optional[list[Any]] = None
    commodity_groups: Optional[list[str]] = None
    capacity_profile: Optional[dict[str, Any]] = None
    min_order_value: Optional[Money] = None
    moq_by_category: Optional[dict[str, Any]] = None
    lead_time_profile: Optional[dict[str, Any]] = None
    payment_terms: Optional[dict[str, Any]] = None
    shipping_terms: Optional[dict[str, Any]] = None
    tax_profile: Optional[dict[str, Any]] = None
    currency_support: Optional[list[str]] = None
    substitute_willingness: Optional[bool] = None
    engineering_support: Optional[bool] = None
    ships_on_their_own: Optional[bool] = None
    platform_integration_level: Optional[VendorPlatformIntegrationLevel] = None


# ──────────────────────────────────────────────────────────────────────────
# VendorUser (contract §2.14)
# ──────────────────────────────────────────────────────────────────────────

class VendorUserSchema(PGIBase):
    """User account belonging to a vendor organization."""

    vendor_user_id: UUID
    vendor_id: UUID
    email: str
    name: Optional[str] = None
    role: VendorUserRole
    status: VendorUserStatus
    mfa_enrolled: bool = False
    last_active_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("email", mode="before")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        return v.strip().lower()


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Part_Capability (contract §2.15)
# ──────────────────────────────────────────────────────────────────────────

class VendorPartCapabilitySchema(PGIBase):
    """Declares a vendor's capability for a specific part or commodity group.

    Constraint: at least one of (part_id, commodity_group) must be non-null.
    confidence_source: historical > declared > inferred (data quality hierarchy).
    """

    capability_id: UUID
    vendor_id: UUID
    part_id: Optional[UUID] = None
    commodity_group: Optional[str] = Field(default=None, max_length=128)
    min_qty: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    max_qty: Optional[Decimal] = None
    lead_time_band: dict[str, Any] = Field(default_factory=dict)
    price_band: dict[str, Any] = Field(default_factory=dict)
    tooling_required: bool = False
    supported_finishes: list[Any] = Field(default_factory=list)
    supported_certifications: list[Any] = Field(default_factory=list)
    confidence_source: VendorCapabilityConfidenceSource
    last_verified: Optional[datetime] = None
    data_freshness_at: datetime

    @model_validator(mode="after")
    def part_or_commodity_required(self) -> "VendorPartCapabilitySchema":
        if self.part_id is None and not self.commodity_group:
            raise ValueError(
                "At least one of (part_id, commodity_group) must be non-null."
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Performance_Snapshot (contract §2.16)
# ──────────────────────────────────────────────────────────────────────────

class VendorPerformanceSnapshotSchema(PGIBase):
    """Nightly-rebuilt vendor performance metrics over a trailing window.

    Rates are in [0, 1]; response_speed_avg is in seconds.
    is_monthly_archive = True for the row preserved on the 1st of each month.
    """

    snapshot_id: UUID
    vendor_id: UUID
    on_time_delivery_rate: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    defect_rate: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    response_speed_avg: Optional[Decimal] = Field(
        default=None, description="Average response time in seconds."
    )
    quote_accuracy: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    doc_completeness: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    ncr_rate: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    snapshot_date: date = Field(description="ISO date string YYYY-MM-DD.")
    orders_in_window: int = 0
    window_days: int = 90
    rebuilt_at: datetime
    is_monthly_archive: bool = False


# ──────────────────────────────────────────────────────────────────────────
# Certification (contract §2.17)
# ──────────────────────────────────────────────────────────────────────────

class CertificationSchema(PGIBase):
    """A vendor certification document.

    custom_type_label is required when type = 'custom'.
    document_url is an S3 presigned URL base — expiry handled server-side.
    """

    certification_id: UUID
    vendor_id: UUID
    type: CertificationType
    custom_type_label: Optional[str] = Field(default=None, max_length=128)
    document_url: str = Field(max_length=1024)
    expiry_date: Optional[date] = Field(default=None, description="ISO date YYYY-MM-DD.")
    verified_by_pgi: bool = False
    verified_at: Optional[datetime] = None
    status: CertificationStatus
    created_at: datetime

    @model_validator(mode="after")
    def custom_label_required(self) -> "CertificationSchema":
        if self.type == CertificationType.CUSTOM and not self.custom_type_label:
            raise ValueError("custom_type_label required when type='custom'.")
        return self


class CertificationUploadRequest(PGIBase):
    """Metadata for POST /api/v1/vendor/certifications (multipart).

    The file itself is a multipart form field; this schema covers the
    non-file metadata fields.
    """

    type: CertificationType
    custom_type_label: Optional[str] = Field(default=None, max_length=128)
    expiry_date: Optional[date] = Field(
        default=None, description="ISO date YYYY-MM-DD."
    )

    @model_validator(mode="after")
    def custom_label_required(self) -> "CertificationUploadRequest":
        if self.type == CertificationType.CUSTOM and not self.custom_type_label:
            raise ValueError("custom_type_label required when type='custom'.")
        return self


class CertificationUploadResponse(PGIBase):
    """Response after uploading a certification document."""

    certification_id: UUID
    status: CertificationStatus  # Always UPLOADED initially


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Profile_Claim (contract §2.18)
# ──────────────────────────────────────────────────────────────────────────

class VendorProfileClaimSchema(PGIBase):
    """A vendor claiming ownership of a ghost profile."""

    claim_id: UUID
    vendor_id: UUID
    claimant_email: str
    claimant_user_id: Optional[UUID] = None
    registration_number: Optional[str] = None
    status: VendorProfileClaimStatus
    verification_evidence_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    resolved_at: Optional[datetime] = None


class VendorClaimRequest(PGIBase):
    """POST /api/v1/vendors/{id}/claim — initiate a vendor profile claim."""

    claimant_email: str = Field(min_length=3, max_length=320)
    registration_number: Optional[str] = Field(default=None, max_length=128)

    @field_validator("claimant_email", mode="before")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        return v.strip().lower()


class VendorClaimResponse(PGIBase):
    """Response confirming that a claim has been initiated."""

    claim_id: UUID
    status: VendorProfileClaimStatus  # Always INITIATED


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Invite (contract §2.19)
# ──────────────────────────────────────────────────────────────────────────

class VendorInviteSchema(PGIBase):
    """Platform invitation sent to a vendor not yet on PGI Hub."""

    invite_id: UUID
    vendor_id: UUID
    invited_by_user_id: UUID
    invitee_email: str
    unique_token: str
    sent_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    created_at: datetime


class VendorInviteRequest(PGIBase):
    """POST /api/v1/vendors/invite — invite a vendor to join PGI Hub."""

    vendor_email: str = Field(min_length=3, max_length=320)
    vendor_name: str = Field(min_length=1, max_length=255)
    country: CountryCode
    commodity_groups: list[str] = Field(min_length=1)

    @field_validator("vendor_email", mode="before")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        return v.strip().lower()


class VendorInviteResponse(PGIBase):
    """Confirmation that a vendor has been invited."""

    vendor_id: UUID
    invite_id: UUID


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Tier_Transition (contract §2.20)
# ──────────────────────────────────────────────────────────────────────────

class VendorTierTransitionSchema(PGIBase):
    """Append-only record of a vendor tier change.

    Used for auditing participation / tier upgrade history.
    """

    transition_id: UUID
    vendor_id: UUID
    from_tier: VendorTier
    to_tier: VendorTier
    triggered_by: VendorTierTransitionTrigger
    reason: Optional[str] = None
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Rating (contract §2.21)
# ──────────────────────────────────────────────────────────────────────────

class VendorRatingSchema(PGIBase):
    """Buyer-submitted star rating for a vendor after a completed PO."""

    rating_id: UUID
    vendor_id: UUID
    rater_user_id: UUID
    po_id: Optional[UUID] = None
    stars: int = Field(ge=1, le=5)
    comment: Optional[str] = None
    created_at: datetime


class VendorRatingCreateRequest(PGIBase):
    """Submit a rating for a vendor."""

    po_id: Optional[UUID] = None
    stars: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=2000)


# ──────────────────────────────────────────────────────────────────────────
# Preferred_Vendor_List and Preferred_Vendor_Member (contract §2.22)
# ──────────────────────────────────────────────────────────────────────────

class PreferredVendorListSchema(PGIBase):
    """An organization's named list of preferred vendors."""

    list_id: UUID
    organization_id: UUID
    name: str = Field(max_length=128)
    created_at: datetime


class PreferredVendorMemberSchema(PGIBase):
    """A vendor's membership in a preferred vendor list.

    Composite PK: (list_id, vendor_id).
    """

    list_id: UUID
    vendor_id: UUID
    added_by_user_id: UUID
    added_at: datetime


class AddPreferredVendorRequest(PGIBase):
    """POST /api/v1/vendors/{id}/preferred — add to preferred list."""

    list_id: Optional[UUID] = Field(
        default=None,
        description="If null, uses the organization's default preferred vendor list.",
    )


class AddPreferredVendorResponse(PGIBase):
    """Confirmation that a vendor was added to the preferred list."""

    added: bool = True


# ──────────────────────────────────────────────────────────────────────────
# Vendor_Filter_Result (contract §2.23)
# ──────────────────────────────────────────────────────────────────────────

class VendorFilterResultSchema(PGIBase):
    """Records why a vendor candidate was eliminated during scoring (step 1 or 2).

    Written by Repo C after the candidate selection phase.
    elimination_step: hard_filter (step 1) or technical_fit_below_threshold (step 2).
    """

    result_id: UUID
    bom_line_id: UUID
    vendor_id: UUID
    vendor_name: Optional[str] = None
    elimination_reason: str = Field(max_length=255)
    elimination_step: VendorFilterEliminationStep
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Vendor portal dashboard (GET /api/v1/vendor/dashboard)
# ──────────────────────────────────────────────────────────────────────────

class VendorDashboardResponse(PGIBase):
    """Vendor-side dashboard — summary of active RFQs, quotes, orders."""

    active_rfqs: list[dict[str, Any]] = Field(default_factory=list)
    active_quotes: list[dict[str, Any]] = Field(default_factory=list)
    active_orders: list[dict[str, Any]] = Field(default_factory=list)
    profile_completion_pct: int = Field(ge=0, le=100)
    unread_messages: int = 0


# Forward reference resolution
VendorDetailResponse.model_rebuild()
