"""Catalog models — maps to catalog.part_master and catalog.alias_table.

These tables are created by migration m003_indexes_and_backfill.py.
ORM models here allow service-layer code to query/insert via SQLAlchemy.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Index, CheckConstraint
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
