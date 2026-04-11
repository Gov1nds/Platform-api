"""
Project-specific Pydantic schemas.

References: GAP-004 (SM-002), api-contract-review.md Section 5.2,
            frontend-backend-contract.md
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    """POST /projects — explicit project creation."""
    name: str
    bom_id: str | None = None
    delivery_location: str | None = None
    target_currency: str = "USD"
    weight_profile: str = "balanced"


class ProjectResponse(BaseModel):
    id: str
    bom_id: str | None = None
    name: str
    status: str
    visibility: str = "owner_only"
    weight_profile: str = "balanced"
    organization_id: str | None = None

    # Denormalized counters
    total_parts: int = 0
    bom_upload_count: int = 0
    bom_line_count: int = 0
    rfq_count: int = 0
    po_count: int = 0

    # Cost
    average_cost: float | None = None
    cost_range_low: float | None = None
    cost_range_high: float | None = None
    lead_time_days: float | None = None

    decision_summary: str | None = None
    file_name: str | None = None
    analyzer_report: dict = {}
    strategy: dict = {}
    events: list[dict] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProjectCursorResponse(BaseModel):
    """Cursor-paginated project list."""
    items: list[ProjectResponse] = []
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total_count: int = 0


class WeightProfileRequest(BaseModel):
    weight_profile: str  # speed_first, cost_first, quality_first, balanced