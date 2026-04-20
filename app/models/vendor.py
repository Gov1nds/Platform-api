"""
Vendor and vendor-adjacent entities.

Contract anchors
----------------
§2.13 Vendor                    §2.15 Vendor_Part_Capability
§2.16 Vendor_Performance_Snapshot §2.17 Certification
§2.18 Vendor_Profile_Claim      §2.19 Vendor_Invite
§2.20 Vendor_Tier_Transition    §2.21 Vendor_Rating
§2.22 Preferred_Vendor_List / Preferred_Vendor_Member

State vocabularies
------------------
§3.10 SM-009 Vendor.participation   §3.11 Vendor.tier
§3.12 SM-010 Vendor_Profile_Claim.status
§3.13 Certification.status          §3.39 Vendor.vendor_type
§3.40 Vendor.platform_integration_level
§3.41 Vendor_Part_Capability.confidence_source
§3.61 Certification.type

Conflict notes
--------------
CN-18: keep both ``participation`` and ``tier`` — participation is
workflow state, tier is buyer-facing capability classification.
Flagged columns: ``vendor.certifications`` JSONB is a denormalized cache;
the authoritative store is the ``certification`` table.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CreatedAtMixin,
    SoftDeleteMixin,
    TimestampMixin,
    country_code,
    enum_check,
    jsonb_array,
    jsonb_object,
    money_default_zero,
    tstz,
    uuid_fk,
    uuid_pk,
)
from app.models.enums import (
    CertificationStatus,
    CertificationType,
    VendorCapabilityConfidenceSource,
    VendorParticipation,
    VendorPlatformIntegrationLevel,
    VendorProfileClaimStatus,
    VendorTier,
    VendorTierTransitionTrigger,
    VendorType,
    values_of,
)


# ─────────────────────────────────────────────────────────────────────────────
# Vendor (§2.13)
# ─────────────────────────────────────────────────────────────────────────────


class Vendor(Base, TimestampMixin, SoftDeleteMixin):
    """Supplier entity — may be a GHOST (unclaimed) or a claimed, fully
    participating supplier on the platform."""

    __tablename__ = "vendor"

    vendor_id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vendor_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'manufacturer'")
    )
    country_of_origin: Mapped[str] = country_code()
    regions_served: Mapped[list] = jsonb_array()
    commodity_groups: Mapped[list] = jsonb_array()
    # FLAGGED: denormalized cache only. Authoritative data = certification table.
    certifications: Mapped[list] = jsonb_array()
    capacity_profile: Mapped[dict] = jsonb_object()
    min_order_value: Mapped[Decimal] = money_default_zero()
    moq_by_category: Mapped[dict] = jsonb_object()
    lead_time_profile: Mapped[dict] = jsonb_object()
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    reliability_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    response_speed: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    payment_terms: Mapped[dict] = jsonb_object()
    shipping_terms: Mapped[dict] = jsonb_object()
    tax_profile: Mapped[dict] = jsonb_object()
    currency_support: Mapped[list] = jsonb_array()
    substitute_willingness: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    engineering_support: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    ships_on_their_own: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    active_status: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    profile_claimed_by: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True
    )
    profile_completion_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    verified_badge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    participation: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'GHOST'")
    )
    tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'GHOST'")
    )
    platform_integration_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'email_only'")
    )
    chat_response_avg_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    last_active_on_platform: Mapped[datetime | None] = tstz(nullable=True)
    onboarded_at: Mapped[datetime | None] = tstz(nullable=True)

    # Relationships
    capabilities: Mapped[list["VendorPartCapability"]] = relationship(
        "VendorPartCapability",
        back_populates="vendor",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    performance_snapshots: Mapped[list["VendorPerformanceSnapshot"]] = relationship(
        "VendorPerformanceSnapshot",
        back_populates="vendor",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    certification_records: Mapped[list["Certification"]] = relationship(
        "Certification",
        back_populates="vendor",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        enum_check("vendor_type", values_of(VendorType)),
        enum_check("participation", values_of(VendorParticipation)),
        enum_check("tier", values_of(VendorTier)),
        enum_check(
            "platform_integration_level", values_of(VendorPlatformIntegrationLevel)
        ),
        CheckConstraint("char_length(country_of_origin) = 2", name="country_of_origin_iso3166"),
        CheckConstraint("min_order_value >= 0", name="min_order_value_nonneg"),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="quality_score_range",
        ),
        CheckConstraint(
            "reliability_score IS NULL "
            "OR (reliability_score >= 0 AND reliability_score <= 100)",
            name="reliability_score_range",
        ),
        CheckConstraint(
            "profile_completion_pct >= 0 AND profile_completion_pct <= 100",
            name="profile_completion_pct_range",
        ),
        Index(
            "ix_vendor_commodity_groups",
            "commodity_groups",
            postgresql_using="gin",
        ),
        Index("ix_vendor_participation", "participation"),
        Index("ix_vendor_tier", "tier"),
        Index("ix_vendor_country_of_origin", "country_of_origin"),
        Index("ix_vendor_active_status", "active_status"),
        Index("ix_vendor_profile_completion_pct", "profile_completion_pct"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorPartCapability (§2.15)
# ─────────────────────────────────────────────────────────────────────────────


class VendorPartCapability(Base, CreatedAtMixin):
    """What a vendor can produce / supply — by specific part or by
    commodity group. At least one of ``part_id`` or ``commodity_group`` is
    non-null."""

    __tablename__ = "vendor_part_capability"

    capability_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    part_id: Mapped[uuid.UUID | None] = uuid_fk(
        "part_master.part_id", ondelete="SET NULL", nullable=True
    )
    commodity_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    min_qty: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default=text("0")
    )
    max_qty: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    lead_time_band: Mapped[dict] = jsonb_object()
    price_band: Mapped[dict] = jsonb_object()
    tooling_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    supported_finishes: Mapped[list] = jsonb_array()
    supported_certifications: Mapped[list] = jsonb_array()
    confidence_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'inferred'")
    )
    last_verified: Mapped[datetime | None] = tstz(nullable=True)
    data_freshness_at: Mapped[datetime] = tstz(default_now=True)

    vendor: Mapped["Vendor"] = relationship(
        "Vendor", back_populates="capabilities", lazy="raise"
    )

    __table_args__ = (
        enum_check("confidence_source", values_of(VendorCapabilityConfidenceSource)),
        CheckConstraint("min_qty >= 0", name="min_qty_nonneg"),
        CheckConstraint(
            "max_qty IS NULL OR max_qty >= min_qty",
            name="max_qty_gte_min_qty",
        ),
        CheckConstraint(
            "part_id IS NOT NULL OR commodity_group IS NOT NULL",
            name="part_or_commodity_required",
        ),
        Index("ix_vendor_part_capability_vendor_id_part_id", "vendor_id", "part_id"),
        Index(
            "ix_vendor_part_capability_vendor_id_commodity_group",
            "vendor_id",
            "commodity_group",
        ),
        Index("ix_vendor_part_capability_confidence_source", "confidence_source"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorPerformanceSnapshot (§2.16)  — nightly rolling-window rebuild
# ─────────────────────────────────────────────────────────────────────────────


class VendorPerformanceSnapshot(Base, CreatedAtMixin):
    """Nightly performance roll-up for a vendor over a configurable window.

    ``is_monthly_archive`` marks rows kept for long-term history after
    rolling-window data ages out.
    """

    __tablename__ = "vendor_performance_snapshot"

    snapshot_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    on_time_delivery_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    defect_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    response_speed_avg: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    quote_accuracy: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    doc_completeness: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    ncr_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    orders_in_window: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("90")
    )
    rebuilt_at: Mapped[datetime] = tstz(default_now=True)
    is_monthly_archive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor", back_populates="performance_snapshots", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint(
            "on_time_delivery_rate IS NULL "
            "OR (on_time_delivery_rate >= 0 AND on_time_delivery_rate <= 1)",
            name="on_time_delivery_rate_range",
        ),
        CheckConstraint(
            "defect_rate IS NULL OR (defect_rate >= 0 AND defect_rate <= 1)",
            name="defect_rate_range",
        ),
        CheckConstraint(
            "quote_accuracy IS NULL OR (quote_accuracy >= 0 AND quote_accuracy <= 1)",
            name="quote_accuracy_range",
        ),
        CheckConstraint(
            "doc_completeness IS NULL OR (doc_completeness >= 0 AND doc_completeness <= 1)",
            name="doc_completeness_range",
        ),
        CheckConstraint(
            "ncr_rate IS NULL OR (ncr_rate >= 0 AND ncr_rate <= 1)",
            name="ncr_rate_range",
        ),
        UniqueConstraint(
            "vendor_id",
            "snapshot_date",
            "is_monthly_archive",
            name="uq_vendor_perf_snapshot_vendor_date_archive",
        ),
        Index(
            "ix_vendor_perf_snapshot_vendor_id_rebuilt_at",
            "vendor_id",
            "rebuilt_at",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Certification (§2.17)
# ─────────────────────────────────────────────────────────────────────────────


class Certification(Base, CreatedAtMixin):
    """Vendor certification document + verification workflow state."""

    __tablename__ = "certification"

    certification_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    custom_type_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    document_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    verified_by_pgi: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    verified_at: Mapped[datetime | None] = tstz(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'UPLOADED'")
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor", back_populates="certification_records", lazy="raise"
    )

    __table_args__ = (
        enum_check("type", values_of(CertificationType)),
        enum_check("status", values_of(CertificationStatus)),
        CheckConstraint(
            "type <> 'custom' OR custom_type_label IS NOT NULL",
            name="custom_type_label_required",
        ),
        Index("ix_certification_vendor_id_status", "vendor_id", "status"),
        Index("ix_certification_expiry_date", "expiry_date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorProfileClaim (§2.18)
# ─────────────────────────────────────────────────────────────────────────────


class VendorProfileClaim(Base):
    """Inbound claim workflow when a vendor wants to take ownership of a
    GHOST profile."""

    __tablename__ = "vendor_profile_claim"

    claim_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    claimant_email: Mapped[str] = mapped_column(String(320), nullable=False)
    claimant_user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "user.user_id", ondelete="SET NULL", nullable=True
    )
    registration_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'INITIATED'")
    )
    verification_evidence_json: Mapped[dict] = jsonb_object()
    created_at: Mapped[datetime] = tstz(default_now=True)
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        enum_check("status", values_of(VendorProfileClaimStatus)),
        Index("ix_vendor_profile_claim_vendor_id", "vendor_id"),
        Index("ix_vendor_profile_claim_status", "status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorInvite (§2.19)
# ─────────────────────────────────────────────────────────────────────────────


class VendorInvite(Base, CreatedAtMixin):
    """Buyer-initiated vendor invitation token."""

    __tablename__ = "vendor_invite"

    invite_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    invited_by_user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    invitee_email: Mapped[str] = mapped_column(String(320), nullable=False)
    unique_token: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime | None] = tstz(nullable=True)
    accepted_at: Mapped[datetime | None] = tstz(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "invitee_email = lower(invitee_email)",
            name="vendor_invite_invitee_email_lowercase",
        ),
        UniqueConstraint("unique_token", name="uq_vendor_invite_unique_token"),
        Index("ix_vendor_invite_vendor_id", "vendor_id"),
        Index("ix_vendor_invite_invitee_email", "invitee_email"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorTierTransition (§2.20) — APPEND-ONLY
# ─────────────────────────────────────────────────────────────────────────────


class VendorTierTransition(Base, CreatedAtMixin):
    """Append-only audit row for every tier change on a vendor profile."""

    __tablename__ = "vendor_tier_transition"

    transition_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    from_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    to_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        enum_check("from_tier", values_of(VendorTier)),
        enum_check("to_tier", values_of(VendorTier)),
        enum_check("triggered_by", values_of(VendorTierTransitionTrigger)),
        CheckConstraint("from_tier <> to_tier", name="vendor_tier_transition_tier_changed"),
        Index("ix_vendor_tier_transition_vendor_id", "vendor_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VendorRating (§2.21)
# ─────────────────────────────────────────────────────────────────────────────


class VendorRating(Base, CreatedAtMixin):
    """Buyer-submitted 1–5 star rating, optionally tied to a specific PO."""

    __tablename__ = "vendor_rating"

    rating_id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = uuid_fk(
        "vendor.vendor_id", ondelete="CASCADE"
    )
    rater_user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    po_id: Mapped[uuid.UUID | None] = uuid_fk(
        "purchase_order.po_id", ondelete="SET NULL", nullable=True
    )
    stars: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="stars_range"),
        UniqueConstraint(
            "vendor_id",
            "rater_user_id",
            "po_id",
            name="uq_vendor_rating_vendor_rater_po",
        ),
        Index(
            "uq_vendor_rating_vendor_rater_no_po",
            "vendor_id",
            "rater_user_id",
            unique=True,
            postgresql_where=text("po_id IS NULL"),
        ),
        Index("ix_vendor_rating_vendor_id", "vendor_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PreferredVendorList / PreferredVendorMember (§2.22)
# ─────────────────────────────────────────────────────────────────────────────


class PreferredVendorList(Base, CreatedAtMixin):
    """Named list of preferred vendors maintained at the organization level."""

    __tablename__ = "preferred_vendor_list"

    list_id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = uuid_fk(
        "organization.organization_id", ondelete="RESTRICT", index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    members: Mapped[list["PreferredVendorMember"]] = relationship(
        "PreferredVendorMember",
        back_populates="preferred_list",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        Index(
            "ix_preferred_vendor_list_organization_id",
            "organization_id",
        ),
    )


class PreferredVendorMember(Base, CreatedAtMixin):
    """Association row between a preferred list and a vendor (composite PK)."""

    __tablename__ = "preferred_vendor_member"

    list_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("preferred_vendor_list.list_id", ondelete="CASCADE"),
        primary_key=True,
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor_id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_by_user_id: Mapped[uuid.UUID] = uuid_fk(
        "user.user_id", ondelete="RESTRICT"
    )
    added_at: Mapped[datetime] = tstz(default_now=True)

    preferred_list: Mapped["PreferredVendorList"] = relationship(
        "PreferredVendorList", back_populates="members", lazy="raise"
    )

    __table_args__ = (
        Index("ix_preferred_vendor_member_vendor_id", "vendor_id"),
    )


__all__ = [
    "Vendor",
    "VendorPartCapability",
    "VendorPerformanceSnapshot",
    "Certification",
    "VendorProfileClaim",
    "VendorInvite",
    "VendorTierTransition",
    "VendorRating",
    "PreferredVendorList",
    "PreferredVendorMember",
]
