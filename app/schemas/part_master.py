"""
part_master.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Part Master & Candidate Match Schema Layer

CONTRACT AUTHORITY: contract.md §2.8 (Part_Master), §2.11 (Candidate_Match).

Notes:
  • pgvector VECTOR(1536) embedding is serialized as list[float] in API responses.
  • search_tokens (TSVECTOR) is not exposed in API responses — internal only.
  • Part_Master is seeded and maintained by Repo C; Repo B receives hints
    on each normalize call but never writes to this table.
  • classification_confidence in [0.0, 1.0].
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import Confidence3, PGIBase


# ──────────────────────────────────────────────────────────────────────────
# Part_Master (contract §2.8)
# ──────────────────────────────────────────────────────────────────────────

class PartMasterResponse(PGIBase):
    """Full Part_Master entity.

    embedding is omitted from standard API responses (large payload);
    returned only when explicitly requested by internal intelligence calls.
    """

    part_id: UUID
    canonical_name: str = Field(max_length=512)
    category: str = Field(max_length=128)
    commodity_group: str = Field(max_length=128)
    taxonomy_code: Optional[str] = Field(default=None, max_length=64)
    spec_template: dict[str, Any] = Field(default_factory=dict)
    default_uom: str = Field(default="pc", max_length=32)
    classification_confidence: Optional[Confidence3] = None
    last_updated: datetime


class PartMasterSummaryResponse(PGIBase):
    """Lightweight Part_Master entry for search results and hints."""

    part_id: UUID
    canonical_name: str
    category: str
    commodity_group: str
    taxonomy_code: Optional[str] = None
    default_uom: str
    classification_confidence: Optional[Confidence3] = None


class PartMasterWithEmbeddingResponse(PartMasterResponse):
    """Part_Master with pgvector embedding — used only in internal Repo B hint assembly.

    embedding: list of 1536 floats (OpenAI text-embedding-3-small or equivalent).
    """

    embedding: Optional[list[float]] = Field(
        default=None,
        description="1536-dimensional embedding vector. Never returned to Repo A.",
    )


class PartMasterCreateRequest(PGIBase):
    """Create a new Part_Master entry (platform-admin or seeding operation)."""

    canonical_name: str = Field(min_length=1, max_length=512)
    category: str = Field(min_length=1, max_length=128)
    commodity_group: str = Field(min_length=1, max_length=128)
    taxonomy_code: Optional[str] = Field(default=None, max_length=64)
    spec_template: dict[str, Any] = Field(default_factory=dict)
    default_uom: str = Field(default="pc", max_length=32)
    classification_confidence: Optional[Confidence3] = None


class PartMasterUpdateRequest(PGIBase):
    """Partial update to a Part_Master entry."""

    canonical_name: Optional[str] = Field(default=None, min_length=1, max_length=512)
    category: Optional[str] = Field(default=None, min_length=1, max_length=128)
    commodity_group: Optional[str] = Field(default=None, min_length=1, max_length=128)
    taxonomy_code: Optional[str] = Field(default=None, max_length=64)
    spec_template: Optional[dict[str, Any]] = None
    default_uom: Optional[str] = Field(default=None, max_length=32)
    classification_confidence: Optional[Confidence3] = None


class PartMasterListResponse(PGIBase):
    """Cursor-paginated list of Part_Master entries."""

    items: list[PartMasterSummaryResponse]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Candidate_Match (contract §2.11)
# ──────────────────────────────────────────────────────────────────────────

class CandidateMatchSchema(PGIBase):
    """A candidate Part_Master match scored by the normalization pipeline.

    Produced by Repo B's normalize step; persisted by Repo C into the
    candidate_match table for audit and downstream use.

    similarity_score: composite [0, 1] from embedding + token overlap.
    match_rank:       1-indexed rank among all candidates for this BOM line.
    """

    match_id: UUID
    bom_line_id: UUID
    part_id: UUID
    similarity_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    match_rank: int = Field(ge=1)
    embedding_distance: Optional[Decimal] = None
    token_overlap: Optional[Decimal] = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    created_at: datetime

    # Denormalized for convenience
    canonical_name: Optional[str] = None
    category: Optional[str] = None


class CandidateMatchListResponse(PGIBase):
    """All candidate matches for a BOM line."""

    bom_line_id: UUID
    matches: list[CandidateMatchSchema]