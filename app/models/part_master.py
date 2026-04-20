"""
Part_Master entity — canonical part catalogue with pgvector embeddings and
PostgreSQL TSVECTOR full-text search tokens.

Contract anchors
----------------
§2.8  Part_Master
§11.18 Job: platform.rebuild_part_master_search_vectors
       (populates ``search_tokens`` + ``embedding`` in place)

Design notes
------------
* ``embedding`` is a pgvector VECTOR(1536) column. Index is created in the
  Alembic migration as either HNSW or IVFFlat (operational choice at deploy
  time); the ORM mapping is index-agnostic.
* ``search_tokens`` is a TSVECTOR column; a GIN index is created in Alembic.
* ``last_updated`` acts as the creation + mutation timestamp (no separate
  ``created_at``/``updated_at`` per spec §2.8).
* Part_Master is version-pinned via ``config_version`` (no TTL).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, jsonb_object, tstz, uuid_pk


class PartMaster(Base, CreatedAtMixin):
    """Canonical part catalogue entry used by normalization and matching."""

    __tablename__ = "part_master"

    part_id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    commodity_group: Mapped[str] = mapped_column(String(128), nullable=False)
    taxonomy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec_template: Mapped[dict] = jsonb_object()
    default_uom: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pc'")
    )
    search_tokens: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    classification_confidence: Mapped = mapped_column(Numeric(4, 3), nullable=True)
    last_updated: Mapped[datetime] = tstz(default_now=True, on_update=True)

    __table_args__ = (
        CheckConstraint(
            "classification_confidence IS NULL "
            "OR (classification_confidence >= 0 AND classification_confidence <= 1)",
            name="classification_confidence_range",
        ),
        Index(
            "ix_part_master_search_tokens",
            "search_tokens",
            postgresql_using="gin",
        ),
        # Embedding index (HNSW preferred; IVFFlat fallback) is declared in
        # Alembic so operators can tune ef_construction / lists at runtime.
        Index("ix_part_master_commodity_group", "commodity_group"),
        Index("ix_part_master_category", "category"),
    )


__all__ = ["PartMaster"]
