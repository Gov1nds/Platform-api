"""Catalog models — maps to catalog.part_master, catalog.alias_table,
catalog.part_attributes, catalog.part_observations, catalog.review_queue.

These tables are created by migration m003_indexes_and_backfill.py + ORM create_all.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Integer, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class PartMaster(Base):
    """Canonical part master — grows from observations, aliases, and review.
    NOT manually populated with millions of rows."""
    __tablename__ = "part_master"
    __table_args__ = (
        Index("ix_part_master_domain", "domain"),
        Index("ix_part_master_category", "category"),
        Index("ix_part_master_mpn", "mpn"),
        Index("ix_part_master_review", "review_status"),
        {"schema": "catalog"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_part_key = Column(Text, nullable=False, unique=True)
    domain = Column(Text, nullable=False, default="unknown")
    category = Column(Text, nullable=True)
    procurement_class = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    material = Column(Text, nullable=True)
    material_grade = Column(Text, nullable=True)
    material_form = Column(Text, nullable=True)
    specs = Column(JSONB, nullable=False, default=dict)
    aliases = Column(JSONB, nullable=False, default=list)
    review_status = Column(Text, nullable=False, default="auto")
    confidence = Column(Numeric(6, 3), nullable=False, default=0)
    source = Column(Text, nullable=False, default="observed")
    observation_count = Column(Numeric(10, 0), nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    alias_entries = relationship("PartAlias", back_populates="part_master", cascade="all, delete-orphan")
    attributes = relationship("PartAttribute", back_populates="part_master", cascade="all, delete-orphan")
    observations = relationship("PartObservation", back_populates="part_master", cascade="all, delete-orphan")


class PartAlias(Base):
    """Alias table — maps MPN variants, supplier PNs, description fragments
    to a canonical PartMaster entry."""
    __tablename__ = "alias_table"
    __table_args__ = (
        Index("ix_alias_type_normalized", "alias_type", "normalized_value"),
        {"schema": "catalog"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    part_master_id = Column(UUID(as_uuid=False), ForeignKey("catalog.part_master.id", ondelete="CASCADE"), nullable=False)
    alias_type = Column(Text, nullable=False)  # mpn, name, supplier_pn, description
    alias_value = Column(Text, nullable=False)
    normalized_value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    part_master = relationship("PartMaster", back_populates="alias_entries")


class PartAttribute(Base):
    """Indexed key/value attributes per canonical part.
    Stronger than JSON specs for large-scale matching and analytics."""
    __tablename__ = "part_attributes"
    __table_args__ = (
        Index("ix_part_attr_key_value", "attribute_key", "attribute_value"),
        Index("ix_part_attr_master", "part_master_id"),
        {"schema": "catalog"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    part_master_id = Column(UUID(as_uuid=False), ForeignKey("catalog.part_master.id", ondelete="CASCADE"), nullable=False)
    attribute_key = Column(Text, nullable=False)   # e.g. "resistance_ohm", "thread_size", "material_grade"
    attribute_value = Column(Text, nullable=False)  # e.g. "10000", "M8", "304"
    attribute_unit = Column(Text, nullable=True)    # e.g. "ohm", "mm"
    numeric_value = Column(Numeric(18, 6), nullable=True)  # For range queries
    source = Column(Text, nullable=False, default="extracted")  # extracted, manual, imported
    confidence = Column(Numeric(6, 3), nullable=False, default=0.8)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    part_master = relationship("PartMaster", back_populates="attributes")


class PartObservation(Base):
    """One row per BOM row that referenced a canonical part.
    Tracks every time this part was seen across all uploaded BOMs."""
    __tablename__ = "part_observations"
    __table_args__ = (
        Index("ix_part_obs_master", "part_master_id"),
        Index("ix_part_obs_bom", "bom_id"),
        Index("ix_part_obs_recorded", "recorded_at"),
        {"schema": "catalog"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    part_master_id = Column(UUID(as_uuid=False), ForeignKey("catalog.part_master.id", ondelete="CASCADE"), nullable=False)
    bom_id = Column(UUID(as_uuid=False), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), nullable=True)
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
    source_file = Column(Text, nullable=True)
    source_sheet = Column(Text, nullable=True)
    source_row = Column(Integer, nullable=True)
    match_score = Column(Numeric(6, 3), nullable=True)
    match_method = Column(Text, nullable=True)  # auto_matched, review_needed, created_new
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    part_master = relationship("PartMaster", back_populates="observations")


class ReviewQueueItem(Base):
    """Explicit review queue for unresolved BOM items.
    Tracks assignment, resolution, and reviewer comments."""
    __tablename__ = "review_queue"
    __table_args__ = (
        Index("ix_review_queue_status", "status"),
        Index("ix_review_queue_category", "category"),
        Index("ix_review_queue_assigned", "assigned_to"),
        {"schema": "catalog"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="CASCADE"), nullable=False)
    bom_id = Column(UUID(as_uuid=False), nullable=False)
    canonical_part_key = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    category = Column(Text, nullable=True)
    match_score = Column(Numeric(6, 3), nullable=True)
    best_candidate_id = Column(UUID(as_uuid=False), nullable=True)
    candidates_json = Column(JSONB, nullable=False, default=list)
    status = Column(Text, nullable=False, default="pending")  # pending, assigned, resolved, promoted, rejected
    assigned_to = Column(UUID(as_uuid=False), nullable=True)  # user ID
    resolution = Column(Text, nullable=True)  # matched, new_canonical, rejected, merged
    resolution_target_id = Column(UUID(as_uuid=False), nullable=True)  # PartMaster ID after resolution
    reviewer_comments = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
