"""RFQ Service — normalized quote lifecycle, comparison, selection, and project synchronization.

Responsibilities:
  - Create RFQ batches from analyzed BOMs
  - Send RFQs and track vendor response deadlines
  - Ingest vendor quotes (normalized headers + line items + legacy compat)
  - Build side-by-side comparison matrices with filtering/sorting
  - Select / reject vendors with project stage advancement
  - Persist immutable comparison snapshots for audit

Integration points:
  - project_service  — stage advancement + event logging
  - analytics_service — (future) spend recording on PO creation
  - email_service     — notification on RFQ send (called from route layer)
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.rfq import (
    RFQBatch,
    RFQItem,
    RFQQuote,
    RFQQuoteHeader,
    RFQQuoteLine,
    RFQComparisonView,
    RFQStatus,
)
from app.models.bom import BOM, BOMPart
from app.models.analysis import AnalysisResult
from app.models.project import Project
from app.models.vendor import Vendor
from app.models.user import User

logger = logging.getLogger("rfq_service")

# Backward-compat alias
RFQ = RFQBatch


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _as_float(value, default=0.0) -> float:
    """Safe float conversion with fallback."""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _as_dt(value):
    """Safe datetime conversion from string or datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _batch_meta_get(rfq: RFQBatch, key: str, default=None):
    """Read a value from rfq.batch_metadata JSONB."""
    return (rfq.batch_metadata or {}).get(key, default)


def _batch_meta_set(rfq: RFQBatch, key: str, value):
    """Write a value into rfq.batch_metadata JSONB."""
    if not rfq.batch_metadata:
        rfq.batch_metadata = {}
    rfq.batch_metadata[key] = value


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT SYNCHRONIZATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_project_for_rfq(db: Session, rfq: RFQBatch) -> Optional[Project]:
    """Resolve the Project linked to an RFQ via project_id or bom_id."""
    if rfq.project_id:
        project = db.query(Project).filter(Project.id == rfq.project_id).first()
        if project:
            return project
    if rfq.bom_id:
        return db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
    return None


def _ensure_project_fields(project: Project, stage: str, next_action: str):
    """Set project workflow fields consistently."""
    if not project:
        return
    project.workflow_stage = stage
    project.status = stage
    if stage in ("draft", "sent", "partial", "quoted", "approved", "rejected", "closed", "error"):
        project.rfq_status = stage
    project.project_metadata = project.project_metadata or {}
    project.project_metadata["workflow_stage"] = stage
    project.project_metadata["next_action"] = next_action


def _advance_project_stage_safe(
    db: Session,
    project: Optional[Project],
    target_stage: str,
    *,
    actor_user_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Attempt to advance the project stage using project_service.advance_project_stage
    if it exists, otherwise return False so caller can fall back to direct writes.
    """
    if not project:
        return False
    try:
        from app.services import project_service as _ps
        if hasattr(_ps, "advance_project_stage"):
            _ps.advance_project_stage(
                db, project, target_stage,
                actor_user_id=actor_user_id,
                payload=payload or {},
            )
            return True
    except (ValueError, ImportError):
        pass
    return False


def _emit_project_event_safe(
    db: Session,
    project: Project,
    event_type: str,
    old_status: Optional[str],
    new_status: Optional[str],
    payload: Optional[Dict[str, Any]] = None,
):
    """Log a project event without crashing on failure."""
    try:
        from app.services import project_service as _ps
        if hasattr(_ps, "record_project_event"):
            _ps.record_project_event(
                db, project, event_type,
                old_status, new_status,
                payload or {},
            )
    except Exception as e:
        logger.warning("Project event emission failed (non-fatal): %s", e)


def _sync_project_rfq_status(
    db: Session,
    rfq: RFQBatch,
    project: Optional[Project],
    target_stage: str,
    next_action: str,
    event_type: str,
    event_payload: Dict[str, Any],
    *,
    actor_user_id: Optional[str] = None,
):
    """
    Unified helper to synchronize project state after an RFQ lifecycle change.
    Attempts advance_project_stage first, falls back to direct field writes.
    """
    if not project:
        return

    old_status = project.workflow_stage or project.status

    # Try state-machine advancement
    advanced = _advance_project_stage_safe(
        db, project, target_stage,
        actor_user_id=actor_user_id,
        payload=event_payload,
    )

    if not advanced:
        # Fallback: direct write + manual event
        _ensure_project_fields(project, target_stage, next_action)
        _emit_project_event_safe(db, project, event_type, old_status, target_stage, event_payload)

    # Always set canonical pointers
    project.current_rfq_id = rfq.id
    project.rfq_status = rfq.status


# ═══════════════════════════════════════════════════════════════════════════════
# RFQ CREATION
# ═══════════════════════════════════════════════════════════════════════════════

def create_rfq_from_analysis(
    db: Session,
    bom_or_project_id: str,
    user_id: Optional[str] = None,
    notes: str = "",
) -> RFQBatch:
    """
    Create an RFQ batch from a BOM or Project ID.

    - Resolves BOM and Project
    - Selects parts requiring RFQ (custom, machined, sheet_metal, rfq_required)
    - Falls back to custom/unknown parts if no rfq_required parts found
    - Creates RFQBatch + RFQItem rows
    - Advances project to rfq_pending
    """
    # ── Resolve BOM + Project ─────────────────────────────────────────────
    bom = db.query(BOM).filter(BOM.id == bom_or_project_id).first()
    project = None
    if not bom:
        project = db.query(Project).filter(Project.id == bom_or_project_id).first()
        if project:
            bom = db.query(BOM).filter(BOM.id == project.bom_id).first()
    if not bom:
        raise ValueError(f"BOM not found: {bom_or_project_id}")

    if not project:
        project = db.query(Project).filter(Project.bom_id == bom.id).first()

    analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
    currency = project.currency if project and getattr(project, "currency", None) else "USD"

    # ── Create RFQ batch ──────────────────────────────────────────────────
    rfq = RFQBatch(
        requested_by_user_id=user_id or bom.uploaded_by_user_id,
        bom_id=bom.id,
        project_id=project.id if project else None,
        guest_session_id=bom.guest_session_id,
        status="draft",
        target_currency=currency,
        notes=notes or "Auto-generated from BOM analysis",
        batch_metadata={
            "total_estimated_cost": float(analysis.average_cost) if analysis and analysis.average_cost else None,
            "quote_status": "draft",
            "response_status": "draft",
        },
    )
    db.add(rfq)
    db.flush()

    # ── Select parts for RFQ ─────────────────────────────────────────────
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom.id).all()
    rfq_parts = [
        p for p in parts
        if p.rfq_required
        or p.procurement_class in ("rfq_required", "custom_manufacture", "sheet_metal", "machined_part")
        or p.is_custom
    ]
    if not rfq_parts:
        rfq_parts = [p for p in parts if p.is_custom or p.procurement_class == "unknown"]

    for p in rfq_parts:
        db.add(RFQItem(
            rfq_batch_id=rfq.id,
            bom_part_id=p.id,
            part_key=p.canonical_name or p.description or "",
            requested_quantity=int(p.quantity) if p.quantity else 1,
            requested_material=p.material or "",
            requested_process=p.process_hint or "",
            drawing_required=p.drawing_required or False,
            canonical_part_key=p.canonical_part_key or "",
        ))

    # ── Advance project stage ─────────────────────────────────────────────
    if project:
        _sync_project_rfq_status(
            db, rfq, project,
            target_stage="rfq_pending",
            next_action="Send RFQ",
            event_type="rfq_created",
            event_payload={"rfq_id": rfq.id, "items": len(rfq_parts)},
            actor_user_id=user_id,
        )

    db.flush()
    db.refresh(rfq)
    logger.info("RFQ created: %s with %d items (of %d total parts)", rfq.id, len(rfq_parts), len(parts))
    return rfq


# ═══════════════════════════════════════════════════════════════════════════════
# RFQ READS
# ═══════════════════════════════════════════════════════════════════════════════

def get_rfq(db: Session, rfq_id: str) -> Optional[RFQBatch]:
    return db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()


def get_user_rfqs(db: Session, user_id: str) -> List[RFQBatch]:
    return db.query(RFQBatch).filter(RFQBatch.requested_by_user_id == user_id).order_by(RFQBatch.created_at.desc()).all()


# ═══════════════════════════════════════════════════════════════════════════════
# RFQ STATUS UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

def update_rfq_status(db: Session, rfq_id: str, status: str) -> Optional[RFQBatch]:
    """Update RFQ status and synchronize the linked project."""
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        return None

    rfq.status = status

    project = _resolve_project_for_rfq(db, rfq)
    if project:
        old_status = project.workflow_stage or project.status
        status_map = {
            "draft": ("rfq_pending", "draft"),
            "sent": ("rfq_sent", "sent"),
            "partial": ("quote_compare", "partial"),
            "quoted": ("quote_compare", "quoted"),
            "approved": ("vendor_selected", "approved"),
            "rejected": ("project_hydrated", "rejected"),
            "closed": ("completed", "closed"),
            "error": ("error", "error"),
        }
        normalized_stage, normalized_rfq = status_map.get(status, (project.workflow_stage or project.status, status))
        next_actions = {
            "rfq_pending": "Send RFQ",
            "rfq_sent": "Collect quotes",
            "quote_compare": "Compare quotes",
            "vendor_selected": "Issue PO",
            "completed": "Closed",
            "error": "Needs attention",
        }
        _ensure_project_fields(project, normalized_stage, next_actions.get(normalized_stage, "Review project"))
        project.rfq_status = normalized_rfq
        project.current_rfq_id = rfq.id
        _emit_project_event_safe(
            db, project, "rfq_status_updated",
            old_status, project.workflow_stage,
            {"rfq_id": rfq.id, "rfq_status": status},
        )

    db.flush()
    db.refresh(rfq)
    return rfq


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTE INGESTION
# ═══════════════════════════════════════════════════════════════════════════════

def _quote_header_payload_from_request(body: Dict[str, Any], vendor_id: Optional[str]) -> Dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "quote_number": body.get("quote_number"),
        "quote_status": body.get("quote_status") or "received",
        "response_status": body.get("response_status") or "received",
        "quote_currency": body.get("quote_currency") or "USD",
        "subtotal": body.get("subtotal"),
        "freight": body.get("freight"),
        "taxes": body.get("taxes"),
        "total": body.get("total"),
        "vendor_response_deadline": _as_dt(body.get("vendor_response_deadline")),
        "sent_at": _as_dt(body.get("sent_at")),
        "received_at": _as_dt(body.get("received_at")),
        "expires_at": _as_dt(body.get("expires_at")),
        "valid_until": _as_dt(body.get("expires_at")) or _as_dt(body.get("vendor_response_deadline")),
        "response_payload": body.get("response_payload") or {},
        "metadata_": body.get("metadata") or {},
    }


def add_quote_to_rfq(
    db: Session,
    rfq_id: str,
    item_quotes: List[Dict[str, Any]],
    vendor_id: Optional[str] = None,
    quote_meta: Optional[Dict[str, Any]] = None,
):
    """
    Ingest a vendor quote response.

    Creates:
      - RFQQuoteHeader (normalized)
      - RFQQuoteLine per matched item (normalized)
      - RFQQuote (legacy compat)

    Updates:
      - RFQItem.spec_summary with quoted data
      - RFQ batch status and metadata
      - Project to quote_compare stage
    """
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    quote_meta = quote_meta or {}
    items = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq_id).all()

    # ── Legacy: update item spec_summary ──────────────────────────────────
    total = 0.0
    for item in items:
        for quote in item_quotes:
            if quote.get("part_name") == item.part_key:
                item.quoted_price = quote.get("price", 0)
                item.lead_time = quote.get("lead_time", 14)
                item.status = quote.get("status", "quoted")
                item.spec_summary = item.spec_summary or {}
                item.spec_summary["availability_status"] = quote.get("availability_status", "unknown")
                item.spec_summary["compliance_status"] = quote.get("compliance_status", "unknown")
                item.spec_summary["moq"] = quote.get("moq")
                item.spec_summary["risk_score"] = quote.get("risk_score")
                total += (_as_float(item.quoted_price, 0.0) * (_as_float(item.requested_quantity, 1.0) or 1.0))
                break

    # ── Normalized quote header ───────────────────────────────────────────
    header = RFQQuoteHeader(
        rfq_batch_id=rfq.id,
        vendor_id=vendor_id,
        quote_number=quote_meta.get("quote_number"),
        quote_status=quote_meta.get("quote_status") or "received",
        response_status=quote_meta.get("response_status") or "received",
        quote_currency=quote_meta.get("quote_currency") or rfq.target_currency or "USD",
        subtotal=_as_float(quote_meta.get("subtotal"), None),
        freight=_as_float(quote_meta.get("freight"), None),
        taxes=_as_float(quote_meta.get("taxes"), None),
        total=_as_float(quote_meta.get("total"), None),
        vendor_response_deadline=_as_dt(quote_meta.get("vendor_response_deadline")),
        sent_at=_as_dt(quote_meta.get("sent_at")),
        received_at=_as_dt(quote_meta.get("received_at")) or datetime.utcnow(),
        expires_at=_as_dt(quote_meta.get("expires_at")),
        valid_until=_as_dt(quote_meta.get("expires_at")) or _as_dt(quote_meta.get("vendor_response_deadline")),
        source_snapshot_id=quote_meta.get("source_snapshot_id"),
        response_payload=quote_meta.get("response_payload") or {},
        metadata_=quote_meta.get("metadata") or {},
    )
    db.add(header)
    db.flush()

    # ── Normalized quote lines ────────────────────────────────────────────
    item_lookup = {i.part_key: i for i in items}
    for quote in item_quotes:
        item = item_lookup.get(quote.get("part_name"))
        if not item:
            continue
        line = RFQQuoteLine(
            quote_header_id=header.id,
            rfq_batch_id=rfq.id,
            rfq_item_id=item.id,
            bom_part_id=item.bom_part_id,
            part_name=item.part_key,
            quantity=_as_float(item.requested_quantity, 1.0),
            unit_price=_as_float(quote.get("price"), None),
            lead_time=_as_float(quote.get("lead_time"), None),
            availability_status=quote.get("availability_status") or "unknown",
            compliance_status=quote.get("compliance_status") or "unknown",
            moq=_as_float(quote.get("moq"), None),
            risk_score=_as_float(quote.get("risk_score"), None),
            line_payload=quote,
        )
        db.add(line)

    # ── Legacy quote row (backward compat) ────────────────────────────────
    legacy_total = _as_float(quote_meta.get("total"), None)
    if legacy_total is None:
        legacy_total = round(total, 2)
    legacy_quote = RFQQuote(
        rfq_batch_id=rfq.id,
        vendor_id=vendor_id,
        quote_number=quote_meta.get("quote_number"),
        status=quote_meta.get("quote_status") or "received",
        quote_currency=quote_meta.get("quote_currency") or rfq.target_currency or "USD",
        subtotal=_as_float(quote_meta.get("subtotal"), None),
        freight=_as_float(quote_meta.get("freight"), None),
        taxes=_as_float(quote_meta.get("taxes"), None),
        total=legacy_total,
        valid_until=_as_dt(quote_meta.get("expires_at")) or _as_dt(quote_meta.get("vendor_response_deadline")),
        received_at=_as_dt(quote_meta.get("received_at")) or datetime.utcnow(),
        source_snapshot_id=quote_meta.get("source_snapshot_id"),
        response_payload=quote_meta.get("response_payload") or {},
        metadata_=quote_meta.get("metadata") or {},
    )
    db.add(legacy_quote)

    # ── Update RFQ batch metadata ─────────────────────────────────────────
    rfq.total_final_cost = legacy_total
    rfq.selected_vendor_id = vendor_id
    rfq.status = "quoted"
    _batch_meta_set(rfq, "quote_status", "quoted")
    _batch_meta_set(rfq, "response_status", "received")
    _batch_meta_set(rfq, "latest_quote_header_id", header.id)

    # ── Advance project to quote_compare ──────────────────────────────────
    project = _resolve_project_for_rfq(db, rfq)
    if project:
        _sync_project_rfq_status(
            db, rfq, project,
            target_stage="quote_compare",
            next_action="Compare quotes",
            event_type="quote_received",
            event_payload={
                "rfq_id": rfq.id,
                "quote_header_id": header.id,
                "vendor_id": vendor_id,
                "total_final_cost": legacy_total,
            },
        )
        project.current_quote_id = header.id

    db.flush()
    db.refresh(rfq)
    return rfq


# ═══════════════════════════════════════════════════════════════════════════════
# RFQ SEND
# ═══════════════════════════════════════════════════════════════════════════════

def send_rfq(
    db: Session,
    rfq_id: str,
    vendor_ids: Optional[List[str]] = None,
    vendor_response_deadline_days: int = 7,
    notes: Optional[str] = None,
):
    """
    Mark RFQ as sent and record vendor response deadline.

    In a production system this would also dispatch emails/webhooks
    to the selected vendors. Currently updates status and metadata.
    """
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    # ── Update RFQ fields ─────────────────────────────────────────────────
    rfq.status = "sent"
    rfq.notes = notes or rfq.notes
    sent_at = datetime.utcnow()
    deadline = sent_at + timedelta(days=max(int(vendor_response_deadline_days or 7), 1))

    rfq.vendor_response_deadline = deadline.isoformat()
    rfq.sent_at = sent_at.isoformat()
    rfq.response_status = "sent"
    rfq.quote_status = "sent"

    if vendor_ids is not None:
        _batch_meta_set(rfq, "recipient_vendor_ids", vendor_ids)
    _batch_meta_set(rfq, "vendor_response_deadline_days", vendor_response_deadline_days)
    _batch_meta_set(rfq, "quote_status", "sent")
    _batch_meta_set(rfq, "response_status", "sent")

    # ── Advance project to rfq_sent ───────────────────────────────────────
    # FIXED: resolve project BEFORE using it
    project = _resolve_project_for_rfq(db, rfq)
    if project:
        _sync_project_rfq_status(
            db, rfq, project,
            target_stage="rfq_sent",
            next_action="Collect quotes",
            event_type="rfq_sent",
            event_payload={
                "rfq_id": rfq.id,
                "vendor_ids": vendor_ids or [],
                "deadline": rfq.vendor_response_deadline,
            },
            actor_user_id=rfq.requested_by_user_id,
        )

    db.flush()
    db.refresh(rfq)
    return rfq


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTE READS & COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def _vendor_latest_header_map(db: Session, rfq_id: str) -> Dict[str, RFQQuoteHeader]:
    """Return the latest quote header per vendor for an RFQ."""
    headers = (
        db.query(RFQQuoteHeader)
        .options(joinedload(RFQQuoteHeader.lines))
        .filter(RFQQuoteHeader.rfq_batch_id == rfq_id)
        .order_by(RFQQuoteHeader.received_at.desc().nullslast(), RFQQuoteHeader.created_at.desc())
        .all()
    )
    latest: Dict[str, RFQQuoteHeader] = {}
    for h in headers:
        key = str(h.vendor_id) if h.vendor_id else str(h.id)
        if key not in latest:
            latest[key] = h
    return latest


def _serialize_quote_header(db: Session, header: RFQQuoteHeader) -> Dict[str, Any]:
    """Serialize a quote header + its lines for API response."""
    vendor = db.query(Vendor).filter(Vendor.id == header.vendor_id).first() if header.vendor_id else None
    return {
        "id": header.id,
        "rfq_batch_id": header.rfq_batch_id,
        "vendor_id": header.vendor_id,
        "vendor_name": vendor.name if vendor else None,
        "quote_number": header.quote_number,
        "quote_status": header.quote_status,
        "response_status": header.response_status,
        "quote_currency": header.quote_currency,
        "subtotal": _as_float(header.subtotal, None),
        "freight": _as_float(header.freight, None),
        "taxes": _as_float(header.taxes, None),
        "total": _as_float(header.total, None),
        "vendor_response_deadline": header.vendor_response_deadline.isoformat() if header.vendor_response_deadline else None,
        "sent_at": header.sent_at.isoformat() if header.sent_at else None,
        "received_at": header.received_at.isoformat() if header.received_at else None,
        "expires_at": header.expires_at.isoformat() if header.expires_at else None,
        "valid_until": header.valid_until.isoformat() if header.valid_until else None,
        "response_payload": header.response_payload or {},
        "metadata": header.metadata_ or {},
        "lines": [
            {
                "id": line.id,
                "quote_header_id": line.quote_header_id,
                "rfq_item_id": line.rfq_item_id,
                "bom_part_id": line.bom_part_id,
                "part_name": line.part_name,
                "quantity": _as_float(line.quantity, 1.0),
                "unit_price": _as_float(line.unit_price, None),
                "lead_time": _as_float(line.lead_time, None),
                "availability_status": line.availability_status,
                "compliance_status": line.compliance_status,
                "moq": _as_float(line.moq, None),
                "risk_score": _as_float(line.risk_score, None),
                "line_payload": line.line_payload or {},
            }
            for line in header.lines
        ],
    }


def get_rfq_quotes(db: Session, rfq_id: str) -> Dict[str, Any]:
    """Return all quote headers for an RFQ, grouped by vendor."""
    rfq = get_rfq(db, rfq_id)
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    headers = (
        db.query(RFQQuoteHeader)
        .options(joinedload(RFQQuoteHeader.lines))
        .filter(RFQQuoteHeader.rfq_batch_id == rfq.id)
        .order_by(RFQQuoteHeader.received_at.desc().nullslast(), RFQQuoteHeader.created_at.desc())
        .all()
    )

    vendor_groups = defaultdict(list)
    for header in headers:
        key = str(header.vendor_id) if header.vendor_id else str(header.id)
        vendor_groups[key].append(_serialize_quote_header(db, header))

    return {
        "rfq_id": rfq.id,
        "status": rfq.status,
        "quote_status": rfq.quote_status,
        "response_status": rfq.response_status,
        "vendor_response_deadline": rfq.vendor_response_deadline,
        "sent_at": rfq.sent_at,
        "received_at": rfq.received_at,
        "expires_at": rfq.expires_at,
        "quote_history": [q for vendor_quotes in vendor_groups.values() for q in vendor_quotes],
        "vendor_groups": dict(vendor_groups),
    }


def build_rfq_comparison(
    db: Session,
    rfq_id: str,
    sort_by: str = "total_cost",
    filters: Optional[Dict[str, Any]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Build a side-by-side comparison matrix of all vendor quotes for an RFQ.

    Output structure:
      vendors: [{vendor_id, vendor_name, total_cost, avg_lead_time, ...}]
      rows:    [{rfq_item_id, part_name, cells: {vendor_id: {price, lead_time, ...}}, best_*}]
      summary: {vendor_count, line_count, best_total_cost_vendor_id, ...}

    Optionally persists an immutable RFQComparisonView snapshot.
    """
    rfq = get_rfq(db, rfq_id)
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")
    filters = filters or {}

    items = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq.id).all()
    latest_headers = _vendor_latest_header_map(db, rfq.id)

    # ── Build vendor-level aggregates ─────────────────────────────────────
    vendors: List[Dict[str, Any]] = []
    for key, header in latest_headers.items():
        vendor = db.query(Vendor).filter(Vendor.id == header.vendor_id).first() if header.vendor_id else None
        lines_map = {str(l.rfq_item_id): l for l in header.lines}
        total_cost = 0.0
        lead_times: List[float] = []
        moqs: List[float] = []
        risks: List[float] = []
        availability_hits = 0
        compliance_hits = 0
        line_count = 0

        for item in items:
            line = lines_map.get(str(item.id))
            if not line:
                continue
            line_count += 1
            qty = _as_float(item.requested_quantity, 1.0)
            unit = _as_float(line.unit_price, 0.0)
            total_cost += qty * unit
            if line.lead_time is not None:
                lead_times.append(_as_float(line.lead_time, 0.0))
            if line.moq is not None:
                moqs.append(_as_float(line.moq, 0.0))
            if line.risk_score is not None:
                risks.append(_as_float(line.risk_score, 0.0))
            if str(line.availability_status).lower() in ("available", "in_stock", "yes", "ok"):
                availability_hits += 1
            if str(line.compliance_status).lower() in ("compliant", "yes", "ok", "pass"):
                compliance_hits += 1

        vendors.append({
            "vendor_id": header.vendor_id,
            "vendor_name": vendor.name if vendor else (header.metadata_.get("vendor_name") if header.metadata_ else None),
            "quote_header_id": header.id,
            "quote_status": header.quote_status,
            "response_status": header.response_status,
            "total_cost": round(total_cost, 6),
            "avg_lead_time": round(sum(lead_times) / len(lead_times), 6) if lead_times else None,
            "vendor_score": _as_float(vendor.reliability_score, 0.0) if vendor else 0.0,
            "moq": min(moqs) if moqs else None,
            "risk_score": round(sum(risks) / len(risks), 6) if risks else None,
            "availability_rate": round(availability_hits / max(line_count, 1), 6),
            "compliance_rate": round(compliance_hits / max(line_count, 1), 6),
            "received_at": header.received_at.isoformat() if header.received_at else None,
            "expires_at": header.expires_at.isoformat() if header.expires_at else None,
            "sent_at": header.sent_at.isoformat() if header.sent_at else None,
        })

    # ── Filter vendors ────────────────────────────────────────────────────
    def _passes(v):
        if filters.get("min_vendor_score") is not None and _as_float(v.get("vendor_score"), 0.0) < _as_float(filters["min_vendor_score"], 0.0):
            return False
        if filters.get("max_cost") is not None and _as_float(v.get("total_cost"), 0.0) > _as_float(filters["max_cost"], 0.0):
            return False
        if filters.get("max_lead_time") is not None and v.get("avg_lead_time") is not None and _as_float(v["avg_lead_time"], 0.0) > _as_float(filters["max_lead_time"], 0.0):
            return False
        if filters.get("max_moq") is not None and v.get("moq") is not None and _as_float(v["moq"], 0.0) > _as_float(filters["max_moq"], 0.0):
            return False
        if filters.get("max_risk") is not None and v.get("risk_score") is not None and _as_float(v["risk_score"], 0.0) > _as_float(filters["max_risk"], 0.0):
            return False
        return True

    vendors = [v for v in vendors if _passes(v)]

    # ── Sort vendors ──────────────────────────────────────────────────────
    sort_by = sort_by or "total_cost"
    sort_configs = {
        "lead_time": lambda x: (x.get("avg_lead_time") is None, x.get("avg_lead_time") or 0),
        "vendor_score": lambda x: -(x.get("vendor_score") or 0),
        "moq": lambda x: (x.get("moq") is None, x.get("moq") or 0),
        "risk": lambda x: (x.get("risk_score") is None, x.get("risk_score") or 0),
    }
    vendors.sort(key=sort_configs.get(sort_by, lambda x: x.get("total_cost") or 0))

    vendor_lookup = {str(v["vendor_id"]): v for v in vendors if v.get("vendor_id")}

    # ── Build per-item rows ───────────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    for item in items:
        cells: Dict[str, Dict[str, Any]] = {}
        best_vendor_id = None
        best_vendor_name = None
        best_price = None
        best_lead = None
        best_total = None

        for vendor_key, header in latest_headers.items():
            vid = str(header.vendor_id)
            if vid not in vendor_lookup:
                continue
            line = next((l for l in header.lines if str(l.rfq_item_id) == str(item.id)), None)
            if not line:
                continue

            cell = {
                "vendor_id": vid,
                "vendor_name": vendor_lookup[vid]["vendor_name"],
                "quote_header_id": header.id,
                "price": _as_float(line.unit_price, None),
                "lead_time": _as_float(line.lead_time, None),
                "availability_status": line.availability_status,
                "compliance_status": line.compliance_status,
                "moq": _as_float(line.moq, None),
                "risk_score": _as_float(line.risk_score, None),
                "quote_status": header.quote_status,
                "response_status": header.response_status,
            }
            cells[vid] = cell

            unit_price = _as_float(line.unit_price, None)
            if unit_price is not None:
                total_line_cost = unit_price * _as_float(item.requested_quantity, 1.0)
                if best_total is None or total_line_cost < best_total:
                    best_total = total_line_cost
                    best_price = unit_price
                    best_lead = _as_float(line.lead_time, None)
                    best_vendor_id = vid
                    best_vendor_name = vendor_lookup[vid]["vendor_name"]

        rows.append({
            "rfq_item_id": str(item.id),
            "bom_part_id": str(item.bom_part_id),
            "part_name": item.part_key,
            "quantity": _as_float(item.requested_quantity, 1.0),
            "material": item.requested_material,
            "process": item.requested_process,
            "cells": cells,
            "best_vendor_id": best_vendor_id,
            "best_vendor_name": best_vendor_name,
            "best_price": best_price,
            "best_lead_time": best_lead,
        })

    # ── Summary ───────────────────────────────────────────────────────────
    summary = {
        "vendor_count": len(vendors),
        "line_count": len(rows),
        "best_total_cost_vendor_id": vendors[0]["vendor_id"] if vendors else None,
        "best_total_cost": vendors[0]["total_cost"] if vendors else None,
        "best_lead_time_vendor_id": (
            min(vendors, key=lambda x: (x.get("avg_lead_time") is None, x.get("avg_lead_time") or 0))["vendor_id"]
            if vendors else None
        ),
    }

    payload = {
        "rfq_id": rfq.id,
        "version": 1,
        "sort_by": sort_by,
        "filters_json": filters,
        "summary_json": summary,
        "comparison_json": {
            "vendors": vendors,
            "rows": rows,
            "summary": summary,
        },
        "vendors": vendors,
        "rows": rows,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    # ── Persist snapshot ──────────────────────────────────────────────────
    if persist:
        current_count = db.query(func.count(RFQComparisonView.id)).filter(
            RFQComparisonView.rfq_batch_id == rfq.id
        ).scalar() or 0
        view = RFQComparisonView(
            rfq_batch_id=rfq.id,
            version=int(current_count) + 1,
            sort_by=sort_by,
            filters_json=filters,
            comparison_json=payload["comparison_json"],
            summary_json=summary,
        )
        db.add(view)
        db.flush()
        payload["version"] = view.version
        payload["id"] = view.id
        payload["created_at"] = view.created_at.isoformat() if view.created_at else None
        payload["updated_at"] = view.updated_at.isoformat() if view.updated_at else None

    return payload


# ═══════════════════════════════════════════════════════════════════════════════
# VENDOR SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

def select_vendor_for_rfq(
    db: Session,
    rfq_id: str,
    vendor_id: Optional[str] = None,
    quote_id: Optional[str] = None,
    reason: Optional[str] = None,
):
    """
    Select a winning vendor for an RFQ.

    - Marks the selected quote header as "selected" / "awarded"
    - Updates RFQ status to "approved"
    - Advances project to "vendor_selected"
    """
    rfq = get_rfq(db, rfq_id)
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    # ── Resolve the selected quote header ─────────────────────────────────
    header = None
    if quote_id:
        header = db.query(RFQQuoteHeader).filter(
            RFQQuoteHeader.id == quote_id,
            RFQQuoteHeader.rfq_batch_id == rfq.id,
        ).first()
    elif vendor_id:
        header = (
            db.query(RFQQuoteHeader)
            .filter(RFQQuoteHeader.rfq_batch_id == rfq.id, RFQQuoteHeader.vendor_id == vendor_id)
            .order_by(RFQQuoteHeader.received_at.desc().nullslast(), RFQQuoteHeader.created_at.desc())
            .first()
        )
    if not header:
        raise ValueError("Quote / vendor not found")

    # FIXED: derive winning vendor from header, not undefined variable
    winning_vendor_id = str(header.vendor_id) if header.vendor_id else vendor_id

    # ── Update RFQ batch ──────────────────────────────────────────────────
    rfq.selected_vendor_id = winning_vendor_id
    rfq.status = "approved"
    _batch_meta_set(rfq, "quote_status", "approved")
    _batch_meta_set(rfq, "response_status", "selected")
    _batch_meta_set(rfq, "selected_quote_header_id", header.id)

    # ── Mark headers: selected vs. not ────────────────────────────────────
    all_headers = db.query(RFQQuoteHeader).filter(RFQQuoteHeader.rfq_batch_id == rfq.id).all()
    for h in all_headers:
        if h.id == header.id:
            h.quote_status = "selected"
            h.response_status = "awarded"
        elif winning_vendor_id and str(h.vendor_id) == winning_vendor_id:
            h.quote_status = "selected"
            h.response_status = "awarded"
        else:
            h.quote_status = h.quote_status or "received"

    # ── Advance project to vendor_selected ────────────────────────────────
    # FIXED: resolve project BEFORE using it in advance call
    project = _resolve_project_for_rfq(db, rfq)
    if project:
        _sync_project_rfq_status(
            db, rfq, project,
            target_stage="vendor_selected",
            next_action="Issue PO",
            event_type="vendor_selected",
            event_payload={
                "rfq_id": rfq.id,
                "vendor_id": winning_vendor_id,
                "quote_id": header.id,
                "reason": reason,
            },
            actor_user_id=rfq.requested_by_user_id,
        )
        project.current_quote_id = header.id
        project.current_vendor_match_id = project.current_vendor_match_id or None

    db.flush()
    db.refresh(rfq)
    return {
        "rfq": rfq,
        "selected_quote_header": header,
        "reason": reason,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VENDOR REJECTION
# ═══════════════════════════════════════════════════════════════════════════════

def reject_vendor_for_rfq(
    db: Session,
    rfq_id: str,
    vendor_id: Optional[str] = None,
    quote_id: Optional[str] = None,
    reason: Optional[str] = None,
):
    """
    Reject a vendor quote. Returns the RFQ to quote_compare stage.
    """
    rfq = get_rfq(db, rfq_id)
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    header = None
    if quote_id:
        header = db.query(RFQQuoteHeader).filter(
            RFQQuoteHeader.id == quote_id,
            RFQQuoteHeader.rfq_batch_id == rfq.id,
        ).first()
    elif vendor_id:
        header = (
            db.query(RFQQuoteHeader)
            .filter(RFQQuoteHeader.rfq_batch_id == rfq.id, RFQQuoteHeader.vendor_id == vendor_id)
            .order_by(RFQQuoteHeader.received_at.desc().nullslast(), RFQQuoteHeader.created_at.desc())
            .first()
        )
    if not header:
        raise ValueError("Quote / vendor not found")

    header.quote_status = "rejected"
    header.response_status = "rejected"
    header.metadata_ = header.metadata_ or {}
    header.metadata_["rejection_reason"] = reason

    rfq.status = "quoted"
    _batch_meta_set(rfq, "quote_status", "quoted")
    _batch_meta_set(rfq, "response_status", "partial")

    project = _resolve_project_for_rfq(db, rfq)
    if project:
        _sync_project_rfq_status(
            db, rfq, project,
            target_stage="quote_compare",
            next_action="Compare quotes",
            event_type="vendor_rejected",
            event_payload={
                "rfq_id": rfq.id,
                "vendor_id": str(header.vendor_id),
                "quote_id": header.id,
                "reason": reason,
            },
        )

    db.flush()
    db.refresh(rfq)
    return {
        "rfq": rfq,
        "rejected_quote_header": header,
        "reason": reason,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FULL RFQ RESPONSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_rfq_response(
    db: Session,
    rfq: RFQBatch,
    comparison_filters: Optional[Dict[str, Any]] = None,
    comparison_sort: str = "total_cost",
) -> Dict[str, Any]:
    """Build the full RFQ response payload including items, quotes, and comparison."""
    items = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq.id).all()
    quote_history = get_rfq_quotes(db, rfq.id)
    comparison = build_rfq_comparison(db, rfq.id, sort_by=comparison_sort, filters=comparison_filters or {}, persist=False)

    return {
        "id": rfq.id,
        "bom_id": rfq.bom_id,
        "project_id": rfq.project_id,
        "status": rfq.status,
        "total_estimated_cost": rfq.total_estimated_cost,
        "total_final_cost": rfq.total_final_cost,
        "currency": rfq.target_currency,
        "notes": rfq.notes,
        "vendor_response_deadline": rfq.vendor_response_deadline,
        "sent_at": rfq.sent_at,
        "received_at": rfq.received_at,
        "expires_at": rfq.expires_at,
        "quote_status": rfq.quote_status,
        "response_status": rfq.response_status,
        "items": [
            {
                "id": i.id,
                "bom_part_id": i.bom_part_id,
                "part_name": i.part_key,
                "quantity": int(i.requested_quantity or 1),
                "material": i.requested_material,
                "process": i.requested_process,
                "quoted_price": i.quoted_price,
                "lead_time": i.lead_time,
                "drawing_required": i.drawing_required,
                "status": i.status,
                "canonical_part_key": i.canonical_part_key,
                "spec_summary": i.spec_summary or {},
            }
            for i in items
        ],
        "quotes": quote_history["quote_history"],
        "comparison": comparison,
    }