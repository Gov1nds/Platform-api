"""
bom.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — BOM Intake & Line Schema Layer

CONTRACT AUTHORITY: contract.md §2.6 (BOM_Upload), §2.7 (BOM_Line),
§4.4 (BOM endpoints) + requirements.yaml domains/intake_and_workspace.

Invariants encoded here:
  • raw_text is IMMUTABLE after insert — validated upstream; never updated.
  • file_hash (SHA-256) is the sole deduplication key per project (UNIQUE).
  • quantity CHECK > 0 on non-DRAFT statuses (enforced at workflow layer).
  • score_cache_json is a DENORMALIZED READ CACHE ONLY (CN-20); never used
    as source-of-truth (vendor_score_cache rows are authoritative).
  • normalization_confidence is in [0.0, 1.0]; auto-commit at >= 0.85 (CN-19).
  • BOM_Line.status transitions are exclusively owned by Repo C (SM-001).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .common import (
    DataFreshnessEnvelope,
    BulkActionKind,
    BOMLineStatus,
    BOMUploadImportStatus,
    BOMUploadSourceType,
    Confidence3,
    CountryCode,
    PGIBase,
    Priority,
    RiskFlagDetail,
    SHA256Hex,
    SourcingMode,
)


# ──────────────────────────────────────────────────────────────────────────
# BOM_Upload (contract §2.6)
# ──────────────────────────────────────────────────────────────────────────

class BOMUploadResponse(PGIBase):
    """BOM file upload record — returned after multipart POST."""

    upload_id: UUID
    project_id: UUID
    source_type: BOMUploadSourceType
    file_name: Optional[str] = None
    file_hash: Optional[str] = Field(default=None, max_length=64, description="SHA-256 hex.")
    import_status: BOMUploadImportStatus
    validation_errors_json: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    column_mapping_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    processed_at: Optional[datetime] = None


class BOMUploadInitResponse(PGIBase):
    """Immediate response to POST /api/v1/projects/{id}/bom/upload (before mapping confirm).

    Returns the proposed column mapping so the user can review and adjust
    before data is committed to bom_line rows.
    """

    upload_id: UUID
    file_hash: str = Field(max_length=64)
    proposed_column_mapping: dict[str, str] = Field(
        description=(
            "Maps detected header names to canonical BOM field names. "
            "Example: {'Part Number': 'manufacturer_part_number', 'Qty': 'quantity'}."
        )
    )
    unresolvable_columns: list[str] = Field(
        default_factory=list,
        description="Column headers that could not be fuzzy-matched to any known field.",
    )


class BOMUploadConfirmMappingRequest(PGIBase):
    """User-confirmed column mapping submitted to finalize ingest."""

    column_mapping_json: dict[str, str] = Field(
        description="User-confirmed mapping from raw header → canonical field name.",
        min_length=1,
    )


class BOMUploadConfirmMappingResponse(PGIBase):
    """Result of the confirmed column mapping and row ingest."""

    upload_id: UUID
    import_status: BOMUploadImportStatus
    row_count: int
    validation_errors_json: list[dict[str, Any]] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# BOM_Line (contract §2.7)
# ──────────────────────────────────────────────────────────────────────────

class BOMLineResponse(PGIBase):
    """Full BOM line entity.

    score_cache_json is DENORMALIZED READ CACHE ONLY (CN-20).
    raw_text is IMMUTABLE — snapshot of original input preserved forever.
    normalization_confidence is in [0.0, 1.0]; auto-commit threshold is >= 0.85.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=False,
    )

    bom_line_id: UUID
    project_id: UUID
    upload_id: Optional[UUID] = None
    part_id: Optional[UUID] = None
    row_number: Optional[int] = None
    raw_text: str = Field(description="IMMUTABLE: original user/file input text.")
    normalized_name: Optional[str] = Field(default=None, max_length=512)
    category: Optional[str] = Field(default=None, max_length=128)
    spec_json: dict[str, Any] = Field(default_factory=dict)
    quantity: Decimal = Field(ge=Decimal("0"))
    unit: Optional[str] = Field(default=None, max_length=32)
    target_country: Optional[CountryCode] = None
    delivery_location: Optional[str] = Field(default=None, max_length=255)
    priority: Priority = Field(default=Priority.NORMAL)
    acceptable_substitutes: list[Any] = Field(default_factory=list)
    required_certifications: list[Any] = Field(default_factory=list)
    manufacturer_part_number: Optional[str] = Field(default=None, max_length=128)
    status: BOMLineStatus
    sourcing_type: Optional[SourcingMode] = None
    normalization_confidence: Optional[Confidence3] = Field(
        default=None,
        description="0.0–1.0; auto-commit threshold >= 0.85 (CN-19).",
    )
    enrichment_json: dict[str, Any] = Field(default_factory=dict)
    score_cache_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Denormalized read cache only (CN-20). Not source-of-truth.",
    )
    created_at: datetime
    updated_at: datetime

    # Denormalized fields populated at list-read time
    best_vendor_name: Optional[str] = None
    best_vendor_score: Optional[float] = None
    risk_flags: list[RiskFlagDetail] = Field(default_factory=list)
    data_freshness: Optional[DataFreshnessEnvelope] = Field(
        default=None,
        description=(
            "Freshness of market data underlying enrichment_json. "
            "Null when the BOM line has not yet been enriched."
        ),
    )


class BOMLineSummaryResponse(PGIBase):
    """Compact BOM line for table views.

    Includes denormalized best_vendor and risk summary for the
    BOM pipeline component table (contract §4.4 GET /bom-lines).
    """

    bom_line_id: UUID
    raw_text: str
    normalized_name: Optional[str] = None
    category: Optional[str] = None
    quantity: Decimal
    unit: Optional[str] = None
    status: BOMLineStatus
    sourcing_type: Optional[SourcingMode] = None
    normalization_confidence: Optional[Confidence3] = None
    priority: Priority
    manufacturer_part_number: Optional[str] = None
    best_vendor_name: Optional[str] = None
    best_vendor_score: Optional[float] = None
    risk_flags: list[RiskFlagDetail] = Field(default_factory=list)
    updated_at: datetime
    enrichment_freshness: Optional[DataFreshnessEnvelope] = Field(
        default=None,
        description=(
            "Freshness of the market data underlying enrichment_json and risk_flags. "
            "Null when the BOM line is not yet enriched."
        ),
    )


class BOMLineListResponse(PGIBase):
    """Cursor-paginated list of BOM lines.

    Supports: ?status=...&category=...&risk_flag=...&vendor_id=...
              &sort=score|lead_time|cost|risk|alphabetical&limit=50&cursor=...
    """

    items: list[BOMLineSummaryResponse]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Typed intake (POST /api/v1/projects/{id}/bom/typed)
# ──────────────────────────────────────────────────────────────────────────

class TypedBOMEntry(PGIBase):
    """A single typed (hand-entered) BOM line entry."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=False,
    )

    raw_text: str = Field(
        min_length=1,
        max_length=2000,
        description="Free-text description; preserved as raw_text.",
    )
    quantity: Decimal = Field(gt=Decimal("0"), description="Required quantity.")
    unit: Optional[str] = Field(default=None, max_length=32)
    priority: Optional[Priority] = None


class TypedBOMRequest(PGIBase):
    """Typed / hand-entered BOM submission."""

    entries: list[TypedBOMEntry] = Field(min_length=1)


class TypedBOMResponse(PGIBase):
    """IDs of newly created BOM lines from typed intake."""

    bom_line_ids: list[UUID]


# ──────────────────────────────────────────────────────────────────────────
# Normalization confirm (POST /api/v1/bom-lines/{id}/normalize/confirm)
# ──────────────────────────────────────────────────────────────────────────

class BOMLineNormalizeConfirmRequest(PGIBase):
    """User correction applied when confidence < 0.85 and line is NEEDS_REVIEW.

    After a successful call, BOM_Line.status transitions NEEDS_REVIEW → NORMALIZED
    and a Normalization_Trace row is written with decision_type='review_edited'
    or 'review_approved' depending on whether fields were changed.
    """

    normalized_name: str = Field(min_length=1, max_length=512)
    category: str = Field(min_length=1, max_length=128)
    spec_json: dict[str, Any] = Field(description="Canonical structured spec.")
    quantity: Decimal = Field(gt=Decimal("0"))
    unit: str = Field(min_length=1, max_length=32)
    manufacturer_part_number: Optional[str] = Field(default=None, max_length=128)
    acceptable_substitutes: list[Any] = Field(default_factory=list)
    required_certifications: list[Any] = Field(default_factory=list)


class BOMLineNormalizeConfirmResponse(PGIBase):
    """Confirmation that the user review has been applied."""

    bom_line_id: UUID
    status: BOMLineStatus  # Always NORMALIZED on success


# ──────────────────────────────────────────────────────────────────────────
# Bulk action (POST /api/v1/projects/{id}/bom-lines/bulk-action)
# ──────────────────────────────────────────────────────────────────────────

class BOMLineBulkActionRequest(PGIBase):
    """Bulk operation applied to a set of BOM lines.

    Actions:
      tag:         Add a tag label to all selected lines (payload: {"tag": str}).
      set_priority: Update priority (payload: {"priority": "LOW|NORMAL|HIGH|URGENT"}).
      exclude:     Mark lines as CANCELLED.
      send_to_rfq: Move lines to RFQ_PENDING status to include in next RFQ.
    """

    bom_line_ids: list[UUID] = Field(min_length=1)
    action: BulkActionKind = Field(
        description="One of: 'tag', 'set_priority', 'exclude', 'send_to_rfq'."
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class BOMLineBulkActionResponse(PGIBase):
    """Result of a bulk operation."""

    applied_to: int = Field(description="Number of lines successfully updated.")
    errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-line errors for lines that could not be updated.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Score recompute (POST /api/v1/bom-lines/{id}/recompute-score)
# ──────────────────────────────────────────────────────────────────────────

class RecomputeScoreRequest(PGIBase):
    """Trigger background re-scoring for a single BOM line.

    No request body required — uses current weight_profile from Project.
    """

    pass


class RecomputeScoreResponse(PGIBase):
    """Acknowledgement that scoring has been enqueued."""

    scoring_run_enqueued: bool = True
