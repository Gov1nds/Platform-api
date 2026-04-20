"""
project.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Project & Workspace Schema Layer

CONTRACT AUTHORITY: contract.md §2.4 (Project), §2.5 (ProjectACL),
§2.91 (WorkspaceDecision), §3.2 (SM-002 Project.state), §3.3 (session/project
type), §4.3 (Sessions & Projects endpoints).

Invariants encoded here:
  • name is required when is_session_or_project = 'project' (contract §2.4 CHECK).
  • weight_profile_custom_json is required when weight_profile = 'custom'.
  • is_session_or_project transitions only session → project (CN-11).
  • stage is required on promotion (POST /promote).
  • Project.state transitions are owned exclusively by Repo C (SM-002).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    CountryCode,
    CurrencyCode,
    PGIBase,
    ProjectACLRole,
    Priority,
    ProjectSessionType,
    ProjectStage,
    ProjectState,
    ProjectWeightProfile,
    WeightProfileValues,
    WorkspaceDecisionStateValue,
)


# ──────────────────────────────────────────────────────────────────────────
# Project entity response
# ──────────────────────────────────────────────────────────────────────────

class ProjectResponse(PGIBase):
    """Full project entity — returned by GET /api/v1/projects/{id}."""

    project_id: UUID
    organization_id: UUID
    created_by: UUID
    name: Optional[str] = Field(
        default=None,
        description="Required when is_session_or_project='project'.",
    )
    target_country: Optional[CountryCode] = None
    target_location: Optional[str] = Field(default=None, max_length=255)
    delivery_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    delivery_lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    target_currency: CurrencyCode = Field(default="USD")
    priority: Priority = Field(default=Priority.NORMAL)
    stage: Optional[ProjectStage] = Field(
        default=None,
        description="Required when is_session_or_project='project'.",
    )
    weight_profile: ProjectWeightProfile = Field(default=ProjectWeightProfile.BALANCED)
    weight_profile_custom_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Required when weight_profile='custom'.",
    )
    is_session_or_project: ProjectSessionType = Field(default=ProjectSessionType.SESSION)
    state: ProjectState = Field(default=ProjectState.DRAFT)
    required_timeline: Optional[str] = Field(
        default=None,
        description="ISO 8601 duration string, e.g. 'P30D'.",
    )
    incoterm_preference: Optional[str] = Field(default=None, max_length=16)
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    # Denormalized summary counts — populated by GET /api/v1/projects/{id}
    bom_line_count: Optional[int] = None
    rfq_count: Optional[int] = None
    active_po_count: Optional[int] = None


class ProjectSummaryResponse(PGIBase):
    """Lightweight project entry for list views."""

    project_id: UUID
    name: Optional[str] = None
    is_session_or_project: ProjectSessionType
    state: ProjectState
    priority: Priority
    bom_line_count: int = 0
    target_currency: CurrencyCode
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/projects
# ──────────────────────────────────────────────────────────────────────────

class ProjectCreateRequest(PGIBase):
    """Create a new project or session container.

    Validation rule (contract §2.4):
      When is_session_or_project = 'project', name AND stage are required.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    target_country: Optional[CountryCode] = None
    target_location: Optional[str] = Field(default=None, max_length=255)
    delivery_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    delivery_lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    target_currency: CurrencyCode = Field(default="USD")
    priority: Priority = Field(default=Priority.NORMAL)
    stage: Optional[ProjectStage] = None
    weight_profile: ProjectWeightProfile = Field(default=ProjectWeightProfile.BALANCED)
    weight_profile_custom_json: Optional[dict[str, Any]] = None
    is_session_or_project: ProjectSessionType = Field(default=ProjectSessionType.SESSION)
    required_timeline: Optional[str] = Field(
        default=None,
        description="ISO 8601 duration string e.g. 'P30D'.",
    )
    incoterm_preference: Optional[str] = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def validate_project_fields(self) -> "ProjectCreateRequest":
        if self.is_session_or_project == ProjectSessionType.PROJECT:
            if not self.name:
                raise ValueError("name is required when is_session_or_project='project'.")
            if self.stage is None:
                raise ValueError("stage is required when is_session_or_project='project'.")
        if self.weight_profile == ProjectWeightProfile.CUSTOM and not self.weight_profile_custom_json:
            raise ValueError(
                "weight_profile_custom_json is required when weight_profile='custom'."
            )
        return self


class ProjectCreateResponse(PGIBase):
    """Response after creating a project or session."""

    project_id: UUID
    is_session_or_project: ProjectSessionType
    state: ProjectState
    created_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# PATCH /api/v1/projects/{id}
# ──────────────────────────────────────────────────────────────────────────

class ProjectUpdateRequest(PGIBase):
    """Partial update to project fields.

    is_session_or_project and project_id are excluded — both are immutable
    after creation (session→project transition handled by /promote).
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    target_country: Optional[CountryCode] = None
    target_location: Optional[str] = Field(default=None, max_length=255)
    delivery_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    delivery_lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    target_currency: Optional[CurrencyCode] = None
    priority: Optional[Priority] = None
    stage: Optional[ProjectStage] = None
    weight_profile: Optional[ProjectWeightProfile] = None
    weight_profile_custom_json: Optional[dict[str, Any]] = None
    required_timeline: Optional[str] = None
    incoterm_preference: Optional[str] = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def validate_custom_weight(self) -> "ProjectUpdateRequest":
        if self.weight_profile == ProjectWeightProfile.CUSTOM and not self.weight_profile_custom_json:
            raise ValueError(
                "weight_profile_custom_json required when weight_profile='custom'."
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# POST /api/v1/projects/{id}/promote  (session → project promotion)
# ──────────────────────────────────────────────────────────────────────────

class ProjectPromoteRequest(PGIBase):
    """Promote a lightweight session to a full project.

    All three fields are required — no defaults.  Repo C validates and then
    flips is_session_or_project from 'session' to 'project'.
    Emits session.promoted_to_project event.
    """

    name: str = Field(min_length=1, max_length=255)
    stage: ProjectStage
    weight_profile: ProjectWeightProfile = Field(default=ProjectWeightProfile.BALANCED)
    weight_profile_custom_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Required when weight_profile='custom'.",
    )

    @model_validator(mode="after")
    def validate_custom_weight(self) -> "ProjectPromoteRequest":
        if self.weight_profile == ProjectWeightProfile.CUSTOM and not self.weight_profile_custom_json:
            raise ValueError(
                "weight_profile_custom_json required when weight_profile='custom'."
            )
        return self


class ProjectPromoteResponse(PGIBase):
    """Confirmation of session-to-project promotion."""

    project_id: UUID
    is_session_or_project: ProjectSessionType  # always 'project' after this call
    state: ProjectState


# ──────────────────────────────────────────────────────────────────────────
# GET /api/v1/projects  (list)
# ──────────────────────────────────────────────────────────────────────────

class ProjectListResponse(PGIBase):
    """Cursor-paginated list of projects / sessions."""

    items: list[ProjectSummaryResponse]
    next_cursor: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# ProjectACL (contract §2.5)
# ──────────────────────────────────────────────────────────────────────────

class ProjectACLEntry(PGIBase):
    """A single per-project role assignment.

    UNIQUE constraint: (project_id, user_id) — one active role per user per project.
    """

    acl_id: UUID
    project_id: UUID
    user_id: UUID
    role: ProjectACLRole
    granted_at: datetime
    granted_by: UUID


class GrantProjectAccessRequest(PGIBase):
    """Grant a user a role on a specific project."""

    user_id: UUID
    role: ProjectACLRole


class UpdateProjectAccessRequest(PGIBase):
    """Update an existing project ACL entry."""

    role: ProjectACLRole


class ProjectACLListResponse(PGIBase):
    """All ACL entries for a project."""

    project_id: UUID
    entries: list[ProjectACLEntry]


# ──────────────────────────────────────────────────────────────────────────
# WorkspaceDecision (contract §2.91)
# ──────────────────────────────────────────────────────────────────────────

class WorkspaceDecisionSchema(PGIBase):
    """Audit record of a workspace session→project transition decision.

    Append-only: one row per promotion event.
    """

    decision_id: UUID
    project_id: UUID
    decided_by: UUID
    from_state: WorkspaceDecisionStateValue = Field(description="Always 'session'.")
    to_state: WorkspaceDecisionStateValue = Field(description="Always 'project'.")
    decided_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# BOM Dashboard summary (GET /api/v1/projects/{id}/bom-dashboard)
# ──────────────────────────────────────────────────────────────────────────

class BOMStatusCount(PGIBase):
    """Count of BOM lines in each status."""

    RAW: int = 0
    NORMALIZING: int = 0
    NORMALIZED: int = 0
    NEEDS_REVIEW: int = 0
    ENRICHING: int = 0
    ENRICHED: int = 0
    SCORING: int = 0
    SCORED: int = 0
    RFQ_PENDING: int = 0
    RFQ_SENT: int = 0
    QUOTED: int = 0
    AWARDED: int = 0
    ORDERED: int = 0
    DELIVERED: int = 0
    CLOSED: int = 0
    CANCELLED: int = 0
    ERROR: int = 0


class RecentActivityItem(PGIBase):
    """Single line in the project recent-activity feed."""

    event_type: str
    entity_type: str
    entity_id: UUID
    actor: Optional[str] = None
    occurred_at: datetime
    summary: str


class BOMDashboardResponse(PGIBase):
    """Response for GET /api/v1/projects/{id}/bom-dashboard."""

    project_id: UUID
    total: int
    by_status: BOMStatusCount
    at_risk: int = Field(description="Lines with at least one HIGH or CRITICAL risk flag.")
    recent_activity: list[RecentActivityItem] = Field(default_factory=list)
