"""
Phase 2B Batch 1A canonical SKU and evidence consolidation data-layer models.

Adds additive data-layer entities only:
- pricing.canonical_sku
- pricing.source_sku_link
- ops.connector_health_metrics
- pricing.canonical_offer_snapshot
- market.canonical_availability_snapshot

These models relate to existing Phase 2A entities without modifying or removing
any Phase 2A tables.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class CanonicalSKU(Base):
    """
    Canonical SKU identity consolidated above Phase 2A source mappings.

    Repo alignment note:
    the existing repo uses `canonical_part_key` on BOM parts and Phase 2A
    mappings. This model uses that exact field to maintain compatibility with
    the current source of truth.
    """
    __tablename__ = "canonical_sku"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_canonical_sku_key"),
        UniqueConstraint(
            "canonical_part_key",
            "manufacturer",
            "normalized_mpn",
            name="uq_canonical_sku_part_mpn",
        ),
        Index("ix_canonical_sku_part_key", "canonical_part_key"),
        Index("ix_canonical_sku_mpn_lookup", "manufacturer", "normalized_mpn"),
        Index("ix_canonical_sku_status", "status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    canonical_key = Column(String(160), nullable=False)
    canonical_part_key = Column(Text, nullable=True)

    manufacturer = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    normalized_mpn = Column(Text, nullable=True)
    canonical_name = Column(Text, nullable=True)
    sku_kind = Column(String(40), nullable=False, default="canonical")
    status = Column(String(40), nullable=False, default="ACTIVE")

    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    consolidation_method = Column(String(40), nullable=False, default="rule_based")
    review_status = Column(String(40), nullable=False, default="AUTO")

    primary_vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    primary_vendor = relationship("Vendor")
    source_links = relationship(
        "SourceSKULink",
        back_populates="canonical_sku",
        cascade="all, delete-orphan",
    )
    offer_snapshots = relationship(
        "CanonicalOfferSnapshot",
        back_populates="canonical_sku",
        cascade="all, delete-orphan",
    )
    availability_snapshots = relationship(
        "CanonicalAvailabilitySnapshot",
        back_populates="canonical_sku",
        cascade="all, delete-orphan",
    )


class SourceSKULink(Base):
    """
    Link table from a canonical SKU to Phase 2A source SKU mappings/offers.
    """
    __tablename__ = "source_sku_link"
    __table_args__ = (
        UniqueConstraint(
            "canonical_sku_id",
            "part_to_sku_mapping_id",
            name="uq_source_sku_link_mapping",
        ),
        UniqueConstraint(
            "canonical_sku_id",
            "source_system",
            "external_sku_key",
            name="uq_source_sku_link_external_key",
        ),
        Index("ix_source_sku_link_canonical_sku_id", "canonical_sku_id"),
        Index("ix_source_sku_link_mapping_id", "part_to_sku_mapping_id"),
        Index("ix_source_sku_link_vendor_id", "vendor_id"),
        Index("ix_source_sku_link_status", "link_status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    canonical_sku_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"),
        nullable=False,
    )
    part_to_sku_mapping_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.part_to_sku_mapping.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_system = Column(String(80), nullable=False, default="unknown")
    external_sku_key = Column(String(200), nullable=False)
    vendor_sku = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    normalized_mpn = Column(Text, nullable=True)
    canonical_part_key = Column(Text, nullable=True)

    link_role = Column(String(40), nullable=False, default="source")
    link_status = Column(String(40), nullable=False, default="ACTIVE")
    match_method = Column(String(40), nullable=False, default="exact")
    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    is_primary = Column(Boolean, nullable=False, default=False)

    source_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    canonical_sku = relationship("CanonicalSKU", back_populates="source_links")
    part_mapping = relationship("PartToSkuMapping")
    vendor = relationship("Vendor")
    offer_snapshots = relationship("CanonicalOfferSnapshot", back_populates="source_sku_link")
    availability_snapshots = relationship(
        "CanonicalAvailabilitySnapshot",
        back_populates="source_sku_link",
    )


class ConnectorHealthMetrics(Base):
    """
    Point-in-time connector health metrics for consolidation/evidence sources.
    """
    __tablename__ = "connector_health_metrics"
    __table_args__ = (
        UniqueConstraint(
            "connector_name",
            "metric_scope",
            "window_started_at",
            "window_ended_at",
            name="uq_connector_health_window",
        ),
        Index("ix_connector_health_name", "connector_name"),
        Index("ix_connector_health_status", "status"),
        Index("ix_connector_health_window", "window_started_at", "window_ended_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    connector_name = Column(String(120), nullable=False)
    metric_scope = Column(String(80), nullable=False, default="global")
    status = Column(String(40), nullable=False, default="UNKNOWN")

    success_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    timeout_count = Column(Integer, nullable=False, default=0)
    throttle_count = Column(Integer, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)

    latency_p50_ms = Column(Integer, nullable=True)
    latency_p95_ms = Column(Integer, nullable=True)
    freshness_lag_seconds = Column(Integer, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)

    window_started_at = Column(DateTime(timezone=True), nullable=False)
    window_ended_at = Column(DateTime(timezone=True), nullable=False)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class CanonicalOfferSnapshot(Base):
    """
    Canonicalized offer evidence snapshot assembled from Phase 2A SKU offers.
    """
    __tablename__ = "canonical_offer_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "canonical_sku_id",
            "source_sku_link_id",
            "source_offer_id",
            "observed_at",
            name="uq_canonical_offer_snapshot_point",
        ),
        Index("ix_canonical_offer_snapshot_canonical_sku_id", "canonical_sku_id"),
        Index("ix_canonical_offer_snapshot_source_offer_id", "source_offer_id"),
        Index("ix_canonical_offer_snapshot_vendor_id", "vendor_id"),
        Index("ix_canonical_offer_snapshot_observed_at", "observed_at"),
        Index("ix_canonical_offer_snapshot_freshness", "freshness_status"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    canonical_sku_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_sku_link_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.source_sku_link.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_offer_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.sku_offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )

    offer_status = Column(String(40), nullable=False, default="ACTIVE")
    currency = Column(String(3), nullable=False, default="USD")
    unit_price = Column(Numeric(20, 8), nullable=True)
    moq = Column(Numeric(20, 8), nullable=True)
    spq = Column(Numeric(20, 8), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    packaging = Column(Text, nullable=True)
    incoterm = Column(String(20), nullable=True)
    country_of_origin = Column(String(3), nullable=True)
    factory_region = Column(Text, nullable=True)
    is_authorized = Column(Boolean, nullable=False, default=False)

    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    consolidation_method = Column(String(40), nullable=False, default="phase2a_evidence")
    freshness_status = Column(String(20), nullable=False, default="FRESH")
    observed_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    evidence_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    canonical_sku = relationship("CanonicalSKU", back_populates="offer_snapshots")
    source_sku_link = relationship("SourceSKULink", back_populates="offer_snapshots")
    source_offer = relationship("SKUOffer")
    vendor = relationship("Vendor")


class CanonicalAvailabilitySnapshot(Base):
    """
    Canonicalized availability evidence snapshot assembled from Phase 2A
    availability snapshots.
    """
    __tablename__ = "canonical_availability_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "canonical_sku_id",
            "inventory_location",
            "snapshot_at",
            "source_availability_snapshot_id",
            name="uq_canonical_availability_snapshot_point",
        ),
        Index("ix_canonical_availability_canonical_sku_id", "canonical_sku_id"),
        Index("ix_canonical_availability_source_offer_id", "source_offer_id"),
        Index(
            "ix_canonical_availability_source_snapshot_id",
            "source_availability_snapshot_id",
        ),
        Index("ix_canonical_availability_snapshot_at", "snapshot_at"),
        Index("ix_canonical_availability_status", "availability_status"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    canonical_sku_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.canonical_sku.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_sku_link_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.source_sku_link.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_offer_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.sku_offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_availability_snapshot_id = Column(
        UUID(as_uuid=False),
        ForeignKey("market.sku_availability_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    availability_status = Column(String(40), nullable=False, default="UNKNOWN")
    available_qty = Column(Numeric(20, 8), nullable=True)
    on_order_qty = Column(Numeric(20, 8), nullable=True)
    allocated_qty = Column(Numeric(20, 8), nullable=True)
    backorder_qty = Column(Numeric(20, 8), nullable=True)
    moq = Column(Numeric(20, 8), nullable=True)
    factory_lead_time_days = Column(Numeric(12, 2), nullable=True)
    inventory_location = Column(Text, nullable=True)

    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    consolidation_method = Column(String(40), nullable=False, default="phase2a_evidence")
    freshness_status = Column(String(20), nullable=False, default="FRESH")
    snapshot_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    evidence_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    canonical_sku = relationship("CanonicalSKU", back_populates="availability_snapshots")
    source_sku_link = relationship("SourceSKULink", back_populates="availability_snapshots")
    source_offer = relationship("SKUOffer")
    source_availability_snapshot = relationship("SKUAvailabilitySnapshot")