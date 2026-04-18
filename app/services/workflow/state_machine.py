"""
Canonical workflow state machines — enforced at API boundary.

Provides guarded transition functions for all lifecycle-bearing entities.
Each transition:
  1. Validates source -> target is legal
  2. Evaluates guard conditions
  3. Writes EventAuditLog (SMP-03)
  4. Commits state change
  5. Logs transition

Batch 4 scope: Project (SM-002) guards, BOM Line (SM-001) guards,
BOM Upload transitions. Later batches add RFQ/Quote/PO/Shipment/Invoice/Vendor.

References: state-machines.md (FSD-01 through FSD-10), SMP-01 through SMP-06
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.enums import (
    BOMLineStatus,
    BOMUploadStatus,
    ProjectStatus,
    SessionStatus,
)
from app.models.bom import BOM, BOMPart
from app.models.events import EventAuditLog
from app.models.project import Project, ProjectEvent

logger = logging.getLogger(__name__)


# -- Shared audit helper (SMP-03) --------------------------------------------

def _audit_transition(
    db: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    from_state: str,
    to_state: str,
    actor_id: str | None = None,
    actor_type: str = "SYSTEM",
    trace_id: str | None = None,
    organization_id: str | None = None,
    payload: dict | None = None,
) -> EventAuditLog:
    """Write an append-only audit record for a state transition."""
    log = EventAuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        from_state=from_state,
        to_state=to_state,
        actor_id=actor_id,
        actor_type=actor_type,
        trace_id=trace_id,
        organization_id=organization_id,
        payload=payload or {},
    )
    db.add(log)
    return log


# == Guard functions -- Project (SM-002) ======================================

def _get_project_bom_ids(db: Session, project: Project) -> list[str]:
    """Helper: collect all BOM IDs for a project."""
    return [
        b.id for b in
        db.query(BOM.id).filter(
            BOM.project_id == project.id,
            BOM.deleted_at.is_(None),
        ).all()
    ]


def _guard_has_bom_lines(db: Session, project: Project) -> bool:
    """DRAFT -> INTAKE_COMPLETE: at least one BOM_Line created (status=RAW or later)."""
    bom_ids = _get_project_bom_ids(db, project)
    if not bom_ids:
        return False
    return db.query(BOMPart).filter(
        BOMPart.bom_id.in_(bom_ids),
        BOMPart.deleted_at.is_(None),
    ).count() > 0


def _guard_has_normalizing_lines(db: Session, project: Project) -> bool:
    """INTAKE_COMPLETE -> ANALYSIS_IN_PROGRESS: at least one line in NORMALIZING state."""
    bom_ids = _get_project_bom_ids(db, project)
    if not bom_ids:
        return False
    return db.query(BOMPart).filter(
        BOMPart.bom_id.in_(bom_ids),
        BOMPart.deleted_at.is_(None),
        BOMPart.status == BOMLineStatus.NORMALIZING,
    ).count() > 0


def _guard_all_lines_scored(db: Session, project: Project) -> bool:
    """
    ANALYSIS_IN_PROGRESS -> ANALYSIS_COMPLETE:
    - No lines in NORMALIZING, ENRICHING, SCORING, or NEEDS_REVIEW
    - At least one line in SCORED or later
    """
    bom_ids = _get_project_bom_ids(db, project)
    if not bom_ids:
        return False

    processing_states = {
        BOMLineStatus.NORMALIZING,
        BOMLineStatus.ENRICHING,
        BOMLineStatus.SCORING,
        BOMLineStatus.NEEDS_REVIEW,
    }
    in_processing = db.query(BOMPart).filter(
        BOMPart.bom_id.in_(bom_ids),
        BOMPart.deleted_at.is_(None),
        BOMPart.status.in_(processing_states),
    ).count()
    if in_processing > 0:
        return False

    scored_or_later = {
        BOMLineStatus.SCORED, BOMLineStatus.RFQ_PENDING, BOMLineStatus.RFQ_SENT,
        BOMLineStatus.QUOTED, BOMLineStatus.AWARDED, BOMLineStatus.ORDERED,
        BOMLineStatus.DELIVERED, BOMLineStatus.CLOSED,
    }
    return db.query(BOMPart).filter(
        BOMPart.bom_id.in_(bom_ids),
        BOMPart.deleted_at.is_(None),
        BOMPart.status.in_(scored_or_later),
    ).count() > 0


def _guard_no_shipped_pos(db: Session, project: Project) -> bool:
    """
    Cancellation guard: no POs in SHIPPED or later state.
    PO model not fully wired in this batch -- stub returns True.
    Will be enforced in Batch 6 when PO routes are implemented.
    """
    return True


def _guard_raw_text_exists(db: Session, line: BOMPart) -> bool:
    """RAW -> NORMALIZING: raw_text is not empty."""
    return bool(line.raw_text)


# == SM-002: Project Lifecycle (12 states) ====================================

GuardFn = Callable[[Session, Any], bool]

PROJECT_TRANSITIONS: dict[tuple[str, str], dict[str, Any]] = {
    # -- Happy path --
    (ProjectStatus.DRAFT, ProjectStatus.INTAKE_COMPLETE): {
        "guard": _guard_has_bom_lines,
    },
    (ProjectStatus.INTAKE_COMPLETE, ProjectStatus.ANALYSIS_IN_PROGRESS): {
        "guard": _guard_has_normalizing_lines,
    },
    (ProjectStatus.ANALYSIS_IN_PROGRESS, ProjectStatus.ANALYSIS_COMPLETE): {
        "guard": _guard_all_lines_scored,
    },
    (ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.SOURCING_ACTIVE): {},
    (ProjectStatus.SOURCING_ACTIVE, ProjectStatus.ORDERING_IN_PROGRESS): {},
    (ProjectStatus.ORDERING_IN_PROGRESS, ProjectStatus.EXECUTION_ACTIVE): {},
    (ProjectStatus.EXECUTION_ACTIVE, ProjectStatus.PARTIALLY_DELIVERED): {},
    (ProjectStatus.PARTIALLY_DELIVERED, ProjectStatus.FULLY_DELIVERED): {},
    (ProjectStatus.FULLY_DELIVERED, ProjectStatus.CLOSED): {},
    # -- Archive (only from CLOSED) --
    (ProjectStatus.CLOSED, ProjectStatus.ARCHIVED): {},
    # -- Cancellation from non-terminal active states --
    (ProjectStatus.DRAFT, ProjectStatus.CANCELLED): {},
    (ProjectStatus.INTAKE_COMPLETE, ProjectStatus.CANCELLED): {},
    (ProjectStatus.ANALYSIS_IN_PROGRESS, ProjectStatus.CANCELLED): {},
    (ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.CANCELLED): {},
    (ProjectStatus.SOURCING_ACTIVE, ProjectStatus.CANCELLED): {
        "guard": _guard_no_shipped_pos,
    },
    (ProjectStatus.ORDERING_IN_PROGRESS, ProjectStatus.CANCELLED): {
        "guard": _guard_no_shipped_pos,
    },
    (ProjectStatus.EXECUTION_ACTIVE, ProjectStatus.CANCELLED): {
        "guard": _guard_no_shipped_pos,
    },
    (ProjectStatus.PARTIALLY_DELIVERED, ProjectStatus.CANCELLED): {
        "guard": _guard_no_shipped_pos,
    },
}

_PROJECT_TERMINAL = {ProjectStatus.CLOSED, ProjectStatus.CANCELLED, ProjectStatus.ARCHIVED}

# Legacy transitions -- kept for backward compatibility with old status values
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
    if (current, target) in PROJECT_TRANSITIONS:
        return True
    return target in LEGACY_TRANSITIONS.get(current, [])


def transition_project(
    db: Session,
    project: Project,
    new_status: str,
    actor_user_id: str | None = None,
    actor_type: str = "USER",
    trace_id: str | None = None,
    payload: dict | None = None,
    skip_guard: bool = False,
) -> Project:
    """
    Validate and execute a project state transition (SM-002).

    Steps (per SMP-01 through SMP-05):
      1. Validates source -> target is a legal transition
      2. Evaluates guard condition (unless skip_guard=True)
      3. Writes EventAuditLog (append-only, SMP-03)
      4. Creates legacy ProjectEvent for backward compat
      5. Mutates entity status and updated_at

    Raises:
      HTTPException 409 -- invalid transition
      HTTPException 400 -- guard failed
    """
    current = project.status
    key = (current, new_status)

    # -- Canonical SM-002 check --
    if key in PROJECT_TRANSITIONS:
        config = PROJECT_TRANSITIONS[key]
        guard = config.get("guard")
        if guard and not skip_guard:
            if not guard(db, project):
                raise HTTPException(
                    400,
                    f"Guard failed for project transition from '{current}' to '{new_status}'",
                )
    elif new_status in LEGACY_TRANSITIONS.get(current, []):
        pass  # Legacy path -- no guards
    else:
        raise HTTPException(
            409,
            f"Cannot transition project from '{current}' to '{new_status}'",
        )

    old = project.status
    project.status = new_status
    project.updated_at = datetime.now(timezone.utc)

    # -- Audit log (SMP-03) -- append-only, immutable --
    _audit_transition(
        db,
        event_type="project.status_changed",
        entity_type="project",
        entity_id=project.id,
        from_state=old,
        to_state=new_status,
        actor_id=actor_user_id,
        actor_type=actor_type,
        trace_id=trace_id,
        organization_id=project.organization_id,
        payload=payload or {},
    )

    # -- Legacy ProjectEvent (backward compat with existing UI) --
    db.add(ProjectEvent(
        project_id=project.id,
        event_type="status_change",
        old_status=old,
        new_status=new_status,
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        payload=payload or {},
    ))

    logger.info(
        "Project %s transitioned: %s -> %s (actor=%s, type=%s)",
        project.id, old, new_status, actor_user_id, actor_type,
    )
    return project


# == BOM Upload state transitions =============================================

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
    actor_type: str = "USER",
    trace_id: str | None = None,
) -> BOM:
    """Validate and execute a BOM upload state transition."""
    current = bom.status
    key = (current, target_status)

    if key not in BOM_UPLOAD_TRANSITIONS:
        raise HTTPException(
            409,
            f"Cannot transition BOM upload from '{current}' to '{target_status}'",
        )

    old = bom.status
    bom.status = target_status
    bom.updated_at = datetime.now(timezone.utc)

    _audit_transition(
        db,
        event_type="bom_upload.status_changed",
        entity_type="bom_upload",
        entity_id=bom.id,
        from_state=old,
        to_state=target_status,
        actor_id=actor_id,
        actor_type=actor_type,
        trace_id=trace_id,
        organization_id=bom.organization_id,
    )

    logger.info("BOM %s transitioned: %s -> %s", bom.id, old, target_status)
    return bom


# == SM-001: BOM Line Lifecycle (17 states) ===================================

BOM_LINE_TRANSITIONS: dict[tuple[str, str], dict[str, Any]] = {
    # -- Intake / normalization --
    (BOMLineStatus.RAW, BOMLineStatus.NORMALIZING): {
        "guard": _guard_raw_text_exists,
    },
    (BOMLineStatus.NORMALIZING, BOMLineStatus.NORMALIZED): {},
    (BOMLineStatus.NORMALIZING, BOMLineStatus.NEEDS_REVIEW): {},
    (BOMLineStatus.NORMALIZING, BOMLineStatus.ERROR): {},
    (BOMLineStatus.NEEDS_REVIEW, BOMLineStatus.NORMALIZED): {},
    (BOMLineStatus.NEEDS_REVIEW, BOMLineStatus.NORMALIZING): {},
    # -- Enrichment --
    (BOMLineStatus.NORMALIZED, BOMLineStatus.ENRICHING): {},
    (BOMLineStatus.ENRICHING, BOMLineStatus.ENRICHED): {},
    (BOMLineStatus.ENRICHING, BOMLineStatus.ERROR): {},
    # -- Scoring --
    (BOMLineStatus.ENRICHED, BOMLineStatus.SCORING): {},
    (BOMLineStatus.SCORING, BOMLineStatus.SCORED): {},
    (BOMLineStatus.SCORING, BOMLineStatus.ERROR): {},
    # -- Downstream (RFQ -> delivery) --
    (BOMLineStatus.SCORED, BOMLineStatus.RFQ_PENDING): {},
    (BOMLineStatus.RFQ_PENDING, BOMLineStatus.RFQ_SENT): {},
    (BOMLineStatus.RFQ_SENT, BOMLineStatus.QUOTED): {},
    (BOMLineStatus.QUOTED, BOMLineStatus.AWARDED): {},
    (BOMLineStatus.AWARDED, BOMLineStatus.ORDERED): {},
    (BOMLineStatus.ORDERED, BOMLineStatus.DELIVERED): {},
    (BOMLineStatus.DELIVERED, BOMLineStatus.CLOSED): {},
    # -- Cancellation from non-terminal active states --
    (BOMLineStatus.RAW, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.NORMALIZED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.ENRICHED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.SCORED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.RFQ_PENDING, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.RFQ_SENT, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.QUOTED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.AWARDED, BOMLineStatus.CANCELLED): {},
    (BOMLineStatus.ORDERED, BOMLineStatus.CANCELLED): {},
    # -- Error recovery --
    (BOMLineStatus.ERROR, BOMLineStatus.RAW): {},
    (BOMLineStatus.ERROR, BOMLineStatus.NORMALIZING): {},
}


def transition_bom_line(
    db: Session,
    bom_line: BOMPart,
    target_status: str,
    actor_id: str | None = None,
    actor_type: str = "SYSTEM",
    trace_id: str | None = None,
    skip_guard: bool = False,
) -> BOMPart:
    """
    Validate and execute a BOM line state transition (SM-001).

    Steps (per SMP-01 through SMP-05):
      1. Validates source -> target is a legal transition
      2. Evaluates guard condition (unless skip_guard=True)
      3. Writes EventAuditLog (append-only)
      4. Mutates entity status and updated_at

    Raises:
      HTTPException 409 -- invalid transition
      HTTPException 400 -- guard failed
    """
    current = bom_line.status
    key = (current, target_status)

    if key not in BOM_LINE_TRANSITIONS:
        raise HTTPException(
            409,
            f"Cannot transition BOM line from '{current}' to '{target_status}'",
        )

    config = BOM_LINE_TRANSITIONS[key]
    guard = config.get("guard")
    if guard and not skip_guard:
        if not guard(db, bom_line):
            raise HTTPException(
                400,
                f"Guard failed for BOM line transition from '{current}' to '{target_status}'",
            )

    old = bom_line.status
    bom_line.status = target_status
    bom_line.updated_at = datetime.now(timezone.utc)

    _audit_transition(
        db,
        event_type="bom_line.status_changed",
        entity_type="bom_line",
        entity_id=bom_line.id,
        from_state=old,
        to_state=target_status,
        actor_id=actor_id,
        actor_type=actor_type,
        trace_id=trace_id,
        organization_id=bom_line.organization_id,
    )

    logger.info(
        "BOMLine %s transitioned: %s -> %s (actor=%s)",
        bom_line.id, old, target_status, actor_id,
    )
    return bom_line


# == Cross-machine state advancement helpers ==================================
#
# These check child entity states and explicitly advance parent entities.
# Per LCA-02: Project state is NOT purely derived -- the system performs
# explicit guarded transitions after checking child states.
# =============================================================================

def check_and_advance_project_to_intake_complete(
    db: Session,
    project: Project,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """
    After BOM lines are created, check if project should advance
    DRAFT -> INTAKE_COMPLETE.

    Returns True if transition was performed.
    """
    if project.status != ProjectStatus.DRAFT:
        return False
    if not _guard_has_bom_lines(db, project):
        return False
    transition_project(
        db, project, ProjectStatus.INTAKE_COMPLETE,
        actor_user_id=actor_id,
        actor_type="SYSTEM",
        trace_id=trace_id,
    )
    return True


def check_and_advance_project_to_analysis(
    db: Session,
    project: Project,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """
    After batch-trigger starts normalizing lines, advance
    INTAKE_COMPLETE -> ANALYSIS_IN_PROGRESS.

    Returns True if transition was performed.
    """
    if project.status != ProjectStatus.INTAKE_COMPLETE:
        return False
    if not _guard_has_normalizing_lines(db, project):
        return False
    transition_project(
        db, project, ProjectStatus.ANALYSIS_IN_PROGRESS,
        actor_user_id=actor_id,
        actor_type="SYSTEM",
        trace_id=trace_id,
    )
    return True


def check_and_advance_project_to_analysis_complete(
    db: Session,
    project: Project,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """
    After all lines reach SCORED, advance
    ANALYSIS_IN_PROGRESS -> ANALYSIS_COMPLETE.

    Returns True if transition was performed.
    """
    if project.status != ProjectStatus.ANALYSIS_IN_PROGRESS:
        return False
    if not _guard_all_lines_scored(db, project):
        return False
    transition_project(
        db, project, ProjectStatus.ANALYSIS_COMPLETE,
        actor_user_id=actor_id,
        actor_type="SYSTEM",
        trace_id=trace_id,
    )
    return True


# -- Legacy stage-gate helpers (retained for backward compat) -----------------

RFQ_ALLOWED_STAGES = {
    "analyzed", "strategy", "vendor_match", "rfq_pending",
    ProjectStatus.ANALYSIS_COMPLETE, ProjectStatus.SOURCING_ACTIVE,
}
PO_ALLOWED_STAGES = {
    "vendor_selected", "negotiation", "quote_compare",
    ProjectStatus.SOURCING_ACTIVE, ProjectStatus.ORDERING_IN_PROGRESS,
}


def enforce_rfq_stage(project: Project) -> None:
    """Ensure project is in a valid stage for RFQ creation."""
    if project.status not in RFQ_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create RFQ from project stage '{project.status}'")


def enforce_po_stage(project: Project) -> None:
    """Ensure project is in a valid stage for PO creation."""
    if project.status not in PO_ALLOWED_STAGES:
        raise HTTPException(400, f"Cannot create PO from project stage '{project.status}'")


# ═══════════════════════════════════════════════════════════════════════════
# PO LIFECYCLE STATE MACHINE (Blueprint Section 13)
# ═══════════════════════════════════════════════════════════════════════════

# Map Blueprint state names to existing POStatus enum values and vice versa
_PO_STATE_ALIASES = {
    "DRAFT": "PO_APPROVED",
    "APPROVED": "PO_APPROVED",
    "ISSUED": "PO_SENT",
    "ACKNOWLEDGED": "VENDOR_ACCEPTED",
    "IN_PRODUCTION": "PRODUCTION_STARTED",
    "QC_HOLD": "QUALITY_CHECK",
    "READY_TO_SHIP": "PACKED",
    "BOOKED": "SHIPPED",
    "IN_TRANSIT": "IN_TRANSIT",
    "CUSTOMS_HOLD": "CUSTOMS",
    "OUT_FOR_DELIVERY": "IN_TRANSIT",
    "DELIVERED": "DELIVERED",
    "GOODS_RECEIPT_CONFIRMED": "GR_CONFIRMED",
    "CANCELLED": "CANCELLED",
    "DISPUTED": "ON_HOLD",
}

VALID_PO_TRANSITIONS = {
    "PO_APPROVED": ["PO_SENT", "CANCELLED"],
    "PO_SENT": ["VENDOR_ACCEPTED", "CANCELLED"],
    "VENDOR_ACCEPTED": ["PRODUCTION_STARTED", "CANCELLED"],
    "PRODUCTION_STARTED": ["QUALITY_CHECK", "PACKED"],
    "QUALITY_CHECK": ["PRODUCTION_STARTED", "CANCELLED"],
    "PACKED": ["SHIPPED"],
    "SHIPPED": ["IN_TRANSIT"],
    "IN_TRANSIT": ["CUSTOMS", "DELIVERED"],
    "CUSTOMS": ["IN_TRANSIT"],
    "DELIVERED": ["GR_CONFIRMED", "ON_HOLD"],
    "GR_CONFIRMED": ["CLOSED"],
    "CLOSED": [],
    "CANCELLED": [],
    "ON_HOLD": ["GR_CONFIRMED", "CANCELLED"],
    "CHANGE_ORDER_PENDING": ["PO_APPROVED", "CANCELLED"],
}


def can_transition_po(current_status: str, new_status: str) -> bool:
    """Check whether a PO state transition is valid."""
    # Resolve aliases
    current = _PO_STATE_ALIASES.get(current_status, current_status)
    target = _PO_STATE_ALIASES.get(new_status, new_status)
    allowed = VALID_PO_TRANSITIONS.get(current, [])
    return target in allowed


def transition_po(
    db: Session,
    po,
    new_status: str,
    actor_user_id: str | None = None,
    notes: str | None = None,
) -> object:
    """
    Transition a PurchaseOrder to a new status.

    Validates the transition, updates the record, writes an audit log entry,
    and dispatches relevant notifications.
    """
    current = po.status or "PO_APPROVED"
    target = _PO_STATE_ALIASES.get(new_status, new_status)

    if not can_transition_po(current, target):
        raise ValueError(
            f"Invalid PO transition: {current} → {target}. "
            f"Allowed: {VALID_PO_TRANSITIONS.get(current, [])}"
        )

    old_status = po.status
    po.status = target

    # Write audit log
    _audit_transition(
        db,
        entity_type="purchase_order",
        entity_id=str(po.id),
        from_state=old_status,
        to_state=target,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    # Dispatch notification for key transitions
    _notify_po_transition(db, po, old_status, target, actor_user_id)

    return po


def _notify_po_transition(db, po, from_state, to_state, actor_user_id):
    """Send notifications for significant PO state changes."""
    notification_map = {
        "SHIPPED": "ORDER_SHIPPED",
        "IN_TRANSIT": "ORDER_SHIPPED",
        "DELIVERED": "ORDER_SHIPPED",
        "CANCELLED": "PO_DELAYED",
    }
    event_type = notification_map.get(to_state)
    if not event_type:
        return
    try:
        from app.services.notification_service import notification_service
        notification_service.send(
            db,
            user_id=actor_user_id,
            event_type=event_type,
            context_data={
                "po_id": str(po.id),
                "po_number": getattr(po, "po_number", ""),
                "from_state": from_state,
                "to_state": to_state,
            },
        )
    except Exception:
        pass
