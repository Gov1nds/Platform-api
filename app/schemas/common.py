"""
Shared schema primitives used across all endpoint modules.

References: api-contract-review.md Section 5.5,
            frontend-backend-contract.md response envelope
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str = ""
    code: str | None = None


class ErrorEnvelope(BaseModel):
    """Standardised error response body."""
    error_code: str
    message: str
    trace_id: str | None = None
    details: list[ErrorDetail] | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Cursor-based paginated list response."""
    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total_count: int = 0


class MoneyField(BaseModel):
    """String-encoded monetary amount with ISO-4217 currency."""
    amount: str  # DECIMAL(20,8) as string
    currency_code: str  # ISO 4217


class FreshnessMetadata(BaseModel):
    """Attached to any response that contains cached/enriched data."""
    fetched_at: str | None = None  # ISO 8601
    ttl_seconds: int | None = None
    freshness_status: str | None = None  # FRESH | STALE | EXPIRED


class PermissionHints(BaseModel):
    """Returned alongside resources so the UI can conditionally render actions."""
    can_edit: bool = False
    can_delete: bool = False
    can_approve: bool = False
    can_create_rfq: bool = False
    can_edit_vendor_profile: bool = False