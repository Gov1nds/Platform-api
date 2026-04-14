"""
Phase 2A enrichment and lookup data-layer models.

This file adds only new data-layer entities required for Batch 1 and does not
modify existing Phase 1 models.

Tables added:
- pricing.part_to_sku_mapping
- pricing.sku_offers
- pricing.sku_offer_price_breaks
- market.sku_availability_snapshots
- market.hs_mapping
- market.lane_rate_bands
- bom.bom_line_dependency_index
- ops.enrichment_run_log
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    Numeric,
    Boolean,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class PartToSkuMapping(Base):
    """
    Maps a normalized/canonical BOM line to one or more vendor SKUs.

    Idempotency strategy:
    - unique vendor_id + vendor_sku
    - unique source_system + source_record_id
    - unique source_record_hash
    """
    __tablename__ = "part_to_sku_mapping"
    __table_args__ = (
        UniqueConstraint("vendor_id", "vendor_sku", name="uq_ptsm_vendor_sku"),
        UniqueConstraint("source_system", "source_record_id", name="uq_ptsm_source_record"),
        UniqueConstraint("source_record_hash", name="uq_ptsm_source_hash"),
        Index("ix_ptsm_bom_part_id", "bom_part_id"),
        Index("ix_ptsm_vendor_id", "vendor_id"),
        Index("ix_ptsm_canonical_part_key", "canonical_part_key"),
        Index("ix_ptsm_mpn_lookup", "manufacturer", "normalized_mpn"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="SET NULL"),
        nullable=True,
    )
    vendor_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.vendors.id", ondelete="SET NULL"),
        nullable=True,
    )

    canonical_part_key = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    normalized_mpn = Column(Text, nullable=True)
    vendor_sku = Column(Text, nullable=False)

    sku_kind = Column(String(40), nullable=False, default="catalog")
    match_method = Column(String(40), nullable=False, default="exact")
    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    is_preferred = Column(Boolean, nullable=False, default=False)

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom_part = relationship("BOMPart")
    vendor = relationship("Vendor")
    offers = relationship(
        "SKUOffer",
        back_populates="part_mapping",
        cascade="all, delete-orphan",
    )


class SKUOffer(Base):
    """
    Vendor/commercial offer for a mapped SKU.

    Idempotency strategy:
    - unique source_system + source_record_id
    - unique source_record_hash
    """
    __tablename__ = "sku_offers"
    __table_args__ = (
        UniqueConstraint("source_system", "source_record_id", name="uq_sku_offer_source_record"),
        UniqueConstraint("source_record_hash", name="uq_sku_offer_source_hash"),
        Index("ix_sku_offers_mapping_id", "part_to_sku_mapping_id"),
        Index("ix_sku_offers_vendor_id", "vendor_id"),
        Index("ix_sku_offers_status", "offer_status"),
        Index("ix_sku_offers_validity", "valid_from", "valid_to"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
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

    offer_name = Column(Text, nullable=True)
    offer_status = Column(String(40), nullable=False, default="ACTIVE")
    currency = Column(String(3), nullable=False, default="USD")
    uom = Column(String(40), nullable=True)
    moq = Column(Numeric(20, 8), nullable=True)
    spq = Column(Numeric(20, 8), nullable=True)
    lead_time_days = Column(Numeric(12, 2), nullable=True)
    packaging = Column(Text, nullable=True)
    incoterm = Column(String(20), nullable=True)
    country_of_origin = Column(String(3), nullable=True)
    factory_region = Column(Text, nullable=True)
    is_authorized = Column(Boolean, nullable=False, default=False)

    freshness_status = Column(String(20), nullable=False, default="FRESH")
    observed_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    part_mapping = relationship("PartToSkuMapping", back_populates="offers")
    vendor = relationship("Vendor")
    price_breaks = relationship(
        "SKUOfferPriceBreak",
        back_populates="sku_offer",
        cascade="all, delete-orphan",
    )
    availability_snapshots = relationship(
        "SKUAvailabilitySnapshot",
        back_populates="sku_offer",
        cascade="all, delete-orphan",
    )


class SKUOfferPriceBreak(Base):
    """
    Quantity-break pricing for an SKU offer.

    Idempotency strategy:
    - unique sku_offer_id + break_qty + currency + valid_from
    - unique source_record_hash
    """
    __tablename__ = "sku_offer_price_breaks"
    __table_args__ = (
        UniqueConstraint(
            "sku_offer_id",
            "break_qty",
            "currency",
            "valid_from",
            name="uq_sku_offer_break_version",
        ),
        UniqueConstraint("source_record_hash", name="uq_sku_offer_break_source_hash"),
        Index("ix_sku_offer_breaks_offer_id", "sku_offer_id"),
        Index("ix_sku_offer_breaks_qty", "break_qty"),
        {"schema": "pricing"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    sku_offer_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.sku_offers.id", ondelete="CASCADE"),
        nullable=False,
    )

    break_qty = Column(Numeric(20, 8), nullable=False)
    unit_price = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    extended_price = Column(Numeric(20, 8), nullable=True)
    price_type = Column(String(40), nullable=False, default="unit")

    valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    sku_offer = relationship("SKUOffer", back_populates="price_breaks")


class SKUAvailabilitySnapshot(Base):
    """
    Point-in-time availability snapshot for an offer.

    Idempotency strategy:
    - unique sku_offer_id + inventory_location + snapshot_at
    - unique source_record_hash
    """
    __tablename__ = "sku_availability_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "sku_offer_id",
            "inventory_location",
            "snapshot_at",
            name="uq_sku_availability_point",
        ),
        UniqueConstraint("source_record_hash", name="uq_sku_availability_source_hash"),
        Index("ix_sku_availability_offer_id", "sku_offer_id"),
        Index("ix_sku_availability_snapshot_at", "snapshot_at"),
        Index("ix_sku_availability_status", "availability_status"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    sku_offer_id = Column(
        UUID(as_uuid=False),
        ForeignKey("pricing.sku_offers.id", ondelete="CASCADE"),
        nullable=False,
    )

    availability_status = Column(String(40), nullable=False, default="UNKNOWN")
    available_qty = Column(Numeric(20, 8), nullable=True)
    on_order_qty = Column(Numeric(20, 8), nullable=True)
    allocated_qty = Column(Numeric(20, 8), nullable=True)
    backorder_qty = Column(Numeric(20, 8), nullable=True)
    moq = Column(Numeric(20, 8), nullable=True)
    factory_lead_time_days = Column(Numeric(12, 2), nullable=True)
    inventory_location = Column(Text, nullable=True)

    freshness_status = Column(String(20), nullable=False, default="FRESH")
    snapshot_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now)

    sku_offer = relationship("SKUOffer", back_populates="availability_snapshots")


class HSMapping(Base):
    """
    HS code classification mapping for a BOM line or canonical part.

    Idempotency strategy:
    - unique source_system + source_record_id
    - unique source_record_hash
    """
    __tablename__ = "hs_mapping"
    __table_args__ = (
        UniqueConstraint("source_system", "source_record_id", name="uq_hs_mapping_source_record"),
        UniqueConstraint("source_record_hash", name="uq_hs_mapping_source_hash"),
        Index("ix_hs_mapping_bom_part_id", "bom_part_id"),
        Index("ix_hs_mapping_canonical_part_key", "canonical_part_key"),
        Index("ix_hs_mapping_hs_code", "hs_code"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="SET NULL"),
        nullable=True,
    )

    canonical_part_key = Column(Text, nullable=True)
    category_code = Column(Text, nullable=True)
    material = Column(Text, nullable=True)

    hs_code = Column(String(32), nullable=False)
    hs_version = Column(String(20), nullable=True)
    jurisdiction = Column(String(3), nullable=True)

    mapping_method = Column(String(40), nullable=False, default="rule_based")
    confidence = Column(Numeric(12, 6), nullable=False, default=0)
    review_status = Column(String(40), nullable=False, default="AUTO")

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom_part = relationship("BOMPart")


class LaneRateBand(Base):
    """
    Indexed freight/route rate band by lane and shipment band.

    Idempotency strategy:
    - unique source_record_hash
    - unique lane + mode + band + effective_from + source_system
    """
    __tablename__ = "lane_rate_bands"
    __table_args__ = (
        UniqueConstraint(
            "origin_country",
            "destination_country",
            "mode",
            "min_weight_kg",
            "max_weight_kg",
            "effective_from",
            "source_system",
            name="uq_lane_rate_band_version",
        ),
        UniqueConstraint("source_record_hash", name="uq_lane_rate_band_source_hash"),
        Index("ix_lane_rate_bands_route", "origin_country", "destination_country", "mode"),
        Index("ix_lane_rate_bands_effective", "effective_from", "effective_to"),
        Index("ix_lane_rate_bands_regions", "origin_region", "destination_region"),
        {"schema": "market"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)

    origin_country = Column(String(3), nullable=False)
    origin_region = Column(Text, nullable=True)
    destination_country = Column(String(3), nullable=False)
    destination_region = Column(Text, nullable=True)
    mode = Column(String(20), nullable=False, default="sea")

    min_weight_kg = Column(Numeric(20, 8), nullable=True)
    max_weight_kg = Column(Numeric(20, 8), nullable=True)
    min_volume_cbm = Column(Numeric(20, 8), nullable=True)
    max_volume_cbm = Column(Numeric(20, 8), nullable=True)

    currency = Column(String(3), nullable=False, default="USD")
    rate_type = Column(String(40), nullable=False, default="per_kg")
    rate_value = Column(Numeric(20, 8), nullable=False)
    min_charge = Column(Numeric(20, 8), nullable=True)
    transit_days_min = Column(Integer, nullable=True)
    transit_days_max = Column(Integer, nullable=True)

    freshness_status = Column(String(20), nullable=False, default="FRESH")
    effective_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    effective_to = Column(DateTime(timezone=True), nullable=True)

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class BOMLineDependencyIndex(Base):
    """
    Stores parent/child or peer dependency relationships between BOM lines.

    Idempotency strategy:
    - unique bom_id + parent_bom_part_id + child_bom_part_id + dependency_type
    - unique source_record_hash
    """
    __tablename__ = "bom_line_dependency_index"
    __table_args__ = (
        UniqueConstraint(
            "bom_id",
            "parent_bom_part_id",
            "child_bom_part_id",
            "dependency_type",
            name="uq_bom_line_dependency_edge",
        ),
        UniqueConstraint("source_record_hash", name="uq_bom_line_dependency_source_hash"),
        Index("ix_bldi_bom_id", "bom_id"),
        Index("ix_bldi_parent", "parent_bom_part_id"),
        Index("ix_bldi_child", "child_bom_part_id"),
        {"schema": "bom"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    child_bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="CASCADE"),
        nullable=False,
    )

    dependency_type = Column(String(40), nullable=False, default="requires")
    dependency_strength = Column(Numeric(12, 6), nullable=False, default=1)
    sequence_no = Column(Integer, nullable=True)
    dependency_metadata = Column(JSONB, nullable=False, default=dict)

    source_system = Column(String(80), nullable=False, default="unknown")
    source_record_id = Column(String(160), nullable=True)
    source_record_hash = Column(String(128), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom = relationship("BOM")
    parent_bom_part = relationship("BOMPart", foreign_keys=[parent_bom_part_id])
    child_bom_part = relationship("BOMPart", foreign_keys=[child_bom_part_id])


class EnrichmentRunLog(Base):
    """
    Tracks a single enrichment stage execution for observability, auditability,
    and idempotent reruns.
    """
    __tablename__ = "enrichment_run_log"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_enrichment_run_idempotency_key"),
        Index("ix_enrichment_run_bom_id", "bom_id"),
        Index("ix_enrichment_run_bom_part_id", "bom_part_id"),
        Index("ix_enrichment_run_project_id", "project_id"),
        Index("ix_enrichment_run_stage_status", "stage", "status"),
        Index("ix_enrichment_run_started_at", "started_at"),
        {"schema": "ops"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    bom_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.boms.id", ondelete="SET NULL"),
        nullable=True,
    )
    bom_part_id = Column(
        UUID(as_uuid=False),
        ForeignKey("bom.bom_parts.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    run_scope = Column(String(40), nullable=False, default="bom_line")
    stage = Column(String(80), nullable=False)
    provider = Column(String(80), nullable=True)
    status = Column(String(40), nullable=False, default="started")

    idempotency_key = Column(String(200), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=1)

    records_written = Column(Integer, nullable=False, default=0)
    records_skipped = Column(Integer, nullable=False, default=0)
    records_failed = Column(Integer, nullable=False, default=0)

    freshness_status = Column(String(20), nullable=True)
    request_hash = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)

    source_system = Column(String(80), nullable=False, default="platform-api")
    source_metadata = Column(JSONB, nullable=False, default=dict)

    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    bom = relationship("BOM")
    bom_part = relationship("BOMPart")
    project = relationship("Project")