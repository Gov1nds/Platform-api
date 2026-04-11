"""
BOM upload and BOM line Pydantic schemas.

References: GAP-011, GAP-002, api-contract-review.md Section 5.4,
            frontend-backend-contract.md
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── BOM Upload ───────────────────────────────────────────────────────────────

class BOMUploadCreateResponse(BaseModel):
    """Response from POST /projects/{pid}/bom-uploads."""
    upload_id: str
    status: str  # PENDING, DUPLICATE
    file_hash: str | None = None
    message: str = ""


class MappingPreviewColumn(BaseModel):
    detected_header: str
    suggested_field: str | None = None
    confidence: float = 0.0
    sample_values: list[str] = []


class MappingPreviewResponse(BaseModel):
    """Response from GET /projects/{pid}/bom-uploads/{uid}/mapping-preview."""
    upload_id: str
    status: str
    detected_columns: list[MappingPreviewColumn] = []
    row_count: int = 0
    preview_rows: list[dict] = []


class ConfirmMappingRequest(BaseModel):
    """POST /projects/{pid}/bom-uploads/{uid}/confirm-mapping."""
    column_mapping: dict[str, str]  # detected_header → canonical_field


class ConfirmMappingResponse(BaseModel):
    upload_id: str
    status: str  # MAPPING_CONFIRMED
    lines_created: int = 0


# ── BOM Line ─────────────────────────────────────────────────────────────────

class BOMLineResponse(BaseModel):
    id: str
    bom_id: str
    status: str
    row_number: int | None = None
    item_id: str = ""
    raw_text: str | None = None
    normalized_text: str | None = None
    description: str | None = None
    quantity: float = 1
    unit: str | None = None
    part_number: str | None = None
    mpn: str | None = None
    manufacturer: str | None = None
    category_code: str | None = None
    procurement_class: str = "unknown"
    material: str | None = None
    specs: dict = {}
    canonical_part_key: str | None = None
    is_custom: bool = False
    rfq_required: bool = False
    review_required: bool = False
    source_type: str = "file"

    # Pipeline output summaries
    normalization_status: str | None = None
    enrichment_status: str | None = None
    scoring_status: str | None = None
    risk_flags: list = []

    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BOMLineDetailResponse(BOMLineResponse):
    """Extended detail including pipeline JSON payloads."""
    normalization_trace_json: dict = {}
    enrichment_json: dict = {}
    score_cache_json: dict = {}
    strategy_json: dict = {}
    data_freshness_json: dict = {}


class BOMLinePatchRequest(BaseModel):
    """PATCH /projects/{pid}/bom-lines/{bid} — review confirm or override."""
    normalized_text: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    category_code: str | None = None
    procurement_class: str | None = None
    review_required: bool | None = None
    review_status: str | None = None  # confirmed, override


class BatchTriggerRequest(BaseModel):
    """POST /projects/{pid}/bom-lines/batch-trigger."""
    line_ids: list[str] | None = None  # None = all eligible
    stages: list[str] = Field(
        default_factory=lambda: ["normalize", "enrich", "score"],
        description="Pipeline stages to trigger",
    )


class BatchTriggerResponse(BaseModel):
    triggered_count: int = 0
    skipped_count: int = 0
    message: str = ""