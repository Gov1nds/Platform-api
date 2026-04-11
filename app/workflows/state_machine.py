"""
Canonical workflow state machines — enforced at API boundary.

Provides transition functions for all entities with state lifecycles.
Batch 3 scope: Project (SM-002), BOM Upload, BOM Line (SM-001).
Later batches will add RFQ (SM-004), Quote (SM-005), PO (SM-006), etc.

References: state-machines.md (FSD-01 through FSD-10), SMP-01 through SMP-06
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.enums import (
    BOMLineStatus,
    BOMUploadStatus,
    ProjectStatus,
    SessionStatus,
)
from app.models.bom import BOM, BOMPart
from app.models.project import Project, ProjectEvent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  SM-002: Project Lifecycle (12 states)
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_TRANSITIONS: dict[tuple[str, str], dict] = {
    # DRAFT → INTAKE_COMPLETE (guard: has at least one BOM upload)
    (ProjectStatus.DRAFT, ProjectStatus.INTAKE_COMPLETE): {},
    # INTAKE_COMPLETE → ANALYSIS_IN_PROGRESS (guard: BOM lines exist)
    (ProjectStatus.INTAKE_COMPLETE, ProjectStatus.ANALYSIS_IN_PROGRESS): {},
    # ANALYSIS_IN_PROGRESS → ANALYSIS_COMPLETE (guard: all lines scored)
    (ProjectStatus.ANALYSIS_IN_PROGRESS, ProjectStatus.ANALYSIS_COMPLETE): {},
    # ANALYSIS_COMPLETE → SOURCING_ACTIVE
    (ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.SOURCING_ACTIVE): {},
    # SOURCING_ACTIVE → ORDERING_IN_PROGRESS
    (ProjectStatus.SOURCING_ACTIVE, ProjectStatus.ORDERING_IN_PROGRESS): {},
    # ORDERING_IN_PROGRESS → EXECUTION_ACTIVE
    (ProjectStatus.ORDERING_IN_PROGRESS, ProjectStatus.EXECUTION_ACTIVE): {},
    # EXECUTION_ACTIVE → PARTIALLY_DELIVERED
    (ProjectStatus.EXECUTION_ACTIVE, ProjectStatus.PARTIALLY_DELIVERED): {},
    # PARTIALLY_DELIVERED → FULLY_DELIVERED
    (ProjectStatus.PARTIALLY_DELIVERED, ProjectStatus.FULLY_DELIVERED): {},
    # FULLY_DELIVERED → CLOSED
    (ProjectStatus.FULLY_DELIVERED, ProjectStatus.CLOSED): {},
    # CLOSED → ARCHIVED
    (ProjectStatus.CLOSED, ProjectStatus.ARCHIVED): {},
    # Cancellation from most states
    (ProjectStatus.DRAFT, ProjectStatus.CANCELLED): {},
    (ProjectStatus.INTAKE_COMPLETE, ProjectStatus.CANCELLED): {},
    (ProjectStatus.ANALYSIS_IN_PROGRESS, ProjectStatus.CANCELLED): {},
    (ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.CANCELLED): {},
    (ProjectStatus.SOURCING_ACTIVE, ProjectStatus.CANCELLED): {},
    (ProjectStatus.ORDERING_IN_PROGRESS, ProjectStatus.CANCELLED): {},
}

# Legacy transitions — kept for backward compatibility with old status values
LEGACY_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["analyzing", "cancelled"],
    "analyzing": ["analyzed", "draft"],
    "analyzed": ["strategy", "vendor_match", "rfq_pending", "cancelled"],
    "strategy": ["vendor_match", "rfq_pending", "cancelled"],
    "vendor_match": ["rfq_pending", "cancelled"],
    "rfq_pending": ["rfq_sent", "cancelled"],
    "rfq_sent": ["quote_compare", "cancelled"],
    "quote_compare": ["negotiation", "vendor_selected", "cancelled"],
    "negotiation": ["vendor_selected", "quote_compare", "cancelled"],
    "vendor_selected": ["po_issued", "cancelled"],
    "po_issued": ["in_production", "cancelled"],
    "in_production": ["qc_inspection", "shipped", "cancelled"],
    "qc_inspection": ["shipped", "in_production", "cancelled"],
    "shipped": ["delivered", "cancelled"],
    "delivered": ["completed"],
    "completed": [],
    "cancelled": [],
}


def can_transition(current: str, target: str) -> bool:
    """Check if a project transition is valid (supports both canonical and legacy)."""
    # Canonical SM-002
    if (current, target) in PROJECT_TRANSITIONS:
        return True
    # Legacy
    return target in LEGACY_TRANSITIONS.get(current, [])


def transition_project(
    db: Session,
    project: Project,
    new_status: str,
    actor_user_id: str | None = None,
    payload: dict | None = None,
) -> Project:
    """
    Validate and execute a project state transition.

    Supports both canonical SM-002 values and legacy status strings.
    """
    current = project.status

    if not can_transition(current, new_status):
        raise HTTPException(
            409,
            f"Cannot transition project from '{current}' to '{new_status}'",
        )

    old = project.status
    project.status = new_status
    project.updated_at = datetime.now(timezone.utc)

    db.add(ProjectEvent(
        project_id=project.id,
        event_type="status_change",
        old_status=old,
        new_status=new_status,
        actor_user_id=actor_user_id,
        payload=payload or {},
    ))

    logger.info(
        "Project %s transitioned: %s → %s (actor=%s)",
        project.id, old, new_status, actor_user_id,
    )
    return project


# ═══════════════════════════════════════════════════════════════════════════════
#  BOM Upload state transitions
# ═══════════════════════════════════════════════════════════════════════════════

BOM_UPLOAD_TRANSITIONS: dict[tuple[str, str], dict] = {
    (BOMUploadStatus.PENDING, BOMUploadStatus.PARSING): {},
    (BOMUploadStatus.PARSING, BOMUploadStatus.AWAITING_MAPPING_CONFIRM): {},
    (BOMUploadStatus.PARSING, BOMUploadStatus.PARSE_FAILED): {},
    (BOMUploadStatus.AWAITING_MAPPING_CONFIRM, BOMUploadStatus.MAPPING_CONFIRMED): {},
    (BOMUploadStatus.MAPPING_CONFIRMED, BOMUploadStatus.INGESTED): {},
}


def transition_bom_upload(
    db: Session,
    bom: BOM,
    target_status: str,
    actor_id: str | None = None,
    actor_type: str = "user",
) -> BOM:
    """Validate and execute a BOM upload state transition."""
    current = bom.status
    key = (current, target_status)

    if key not in BOM_UPLOAD_TRANSITIONS:
        raise HTTPException(
            409,
            f"Cannot transition BOM upload from '{current}' to '{target_status}'",
        )

    bom.status = target_status
    bom.updated_at = datetime.now(timezone.utc)
    logger.info("BOM %s transitioned: %s → %s", bom.id, current, target_status)
    return bom


# ═══════════════════════════════════════════════════════════════════════════════
#  SM-001: BOM Line Lifecycle (17 states)
# ═══════════════════════════════════════════════════════════════════════════════

BOM_LINE_TRANSITIONS: dict[tuple[str, str], dict] = {
    # Intake / normalization
    (BOMLineStatus.RAW, BOMLineStatus.NORMALIZING): {},
    (BOMLineStatus.NORMALIZING, BOMLineStatus.NORMALIZED): {},
    (BOMLineStatus.NORMALIZING, BOMLineStatus.NEEDS_REVIEW): {},
    (BOMLineStatus.NORMALIZING, BOMLineStatus.ERROR): {},
    (BOMLineStatus.NEEDS_REVIEW, BOMLineStatus.NORMALIZED): {},
    (BOMLineStatus.NEEDS_REVIEW, BOMLineStatus.NORMALIZING): {},
    # Enrichment
    (BOMLineStatus.NORMALIZED, BOMLineStatus.ENRICHING): {},
    (BOMLineStatus.ENRICHING, BOMLineStatus.ENRICHED): {},
    (BOMLineStatus.ENRICHING, BOMLineStatus.ERROR): {},
    # Scoring
    (BOMLineStatus.ENRICHED, BOMLineStatus.SCORING): {},
    (BOMLineStatus.SCORING, BOMLineStatus.SCORED): {},
    (BOMLineStatus.SCORING, BOMLineStatus.ERROR): {},
    # Downstream (RFQ → delivery)
    (BOMLineStatus.SCORED, BOMLineStatus.RFQ_PENDING): {},
    (BOMLineStatus.RFQ_PENDING, BOMLineStatus.RFQ_SENT): {},
    (BOMLineStatus.RFQ_SENT, BOMLineStatus.QUOTED): {},
    (BOMLineStatus.QUOTED, BOMLineStatus.AWARDED): {},
    (BOMLineStatus.AWARDED, BOMLineStatus.ORDERED): {},
    (BOMLineStatus.ORDERED, BOMLineStatus.DELIVERED): {},
    (BOMLineStatus.DELIVERED, BOMLineStatus.CLOSED): {},
    # Cancellation from most states
    (BOMLineStatus.RAW, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.NORMALIZED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.ENRICHED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.SCORED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.RFQ_PENDING, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.RFQ_SENT, BOMLineStatus.CANCELLED): {},
    # Error recovery
    (BOMLineStatus.ERROR, BOMLineStatus.RAW): {},
    (BOMLineStatus.ERROR, BOMLineStatus.NORMALIZING): {},
}


def transition_bom_line(
    db: Session,
    bom_line: BOMPart,
    target_status: str,
    actor_id: str | None = None,
    actor_type: str = "user",
    trace_id: str | None = None,
) -> BOMPart:
    """Validate and execute a BOM line state transition (SM-001)."""
    current = bom_line.status
    key = (current, target_status)

    if key not in BOM_LINE_TRANSITIONS:
        raise HTTPException(
            409,
            f"Cannot transition BOM line from '{current}' to '{target_status}'",
        )

    bom_line.status = target_status
    bom_line.updated_at = datetime.now(timezone.utc)
    logger.info(
        "BOMLine %s transitioned: %s → %s (actor=%s)",
        bom_line.id, current, target_status, actor_id,
    )
    return bom_line


# ── Legacy helpers (retained for backward compat) ────────────────────────────

RFQ_ALLOWED_STAGES = {
    "analyzed", "strategy", "vendor_match", "rfq_pending",
    ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.SOURCING_ACTIVE,
}
PO_ALLOWED_STAGES = {
    "vendor_selected", "negotiation", "quote_compare",
    ProjectStatus.SOURCING_ACTIVE, ProjectStatus.ORDERING_IN_PROGRESS,
}


def enforce_rfq_stage(project: Project) -> None:
    if project.status not in RFQ_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create RFQ from project stage '{project.status}'")


def enforce_po_stage(project: Project) -> None:
    if project.status not in PO_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create PO from project stage '{project.status}'")