"""RFQ Service — updated for sourcing.rfq_batches PostgreSQL schema."""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.services import project_service
from app.models.rfq import RFQBatch, RFQItem, RFQQuote, RFQStatus
from app.models.bom import BOM, BOMPart
from app.models.analysis import AnalysisResult
from app.models.project import Project

logger = logging.getLogger("rfq_service")

# Alias for backward compat
RFQ = RFQBatch


def create_rfq_from_analysis(
    db: Session,
    bom_or_project_id: str,
    user_id: Optional[str] = None,
    notes: str = "",
) -> RFQBatch:
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

    currency = "USD"
    if project and project.currency:
        currency = project.currency

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
        },
    )
    db.add(rfq)
    db.flush()

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

    if project:
        project.rfq_status = "draft"
        project.workflow_stage = "rfq_pending"
        project.status = "rfq_pending"
        project.current_rfq_id = rfq.id
        project.visibility_level = project.visibility_level or "full"
        project.project_metadata = project.project_metadata or {}
        project.project_metadata["workflow_stage"] = "rfq_pending"
        project.project_metadata["next_action"] = "Send RFQ"
        project_service.record_project_event(
            db,
            project,
            "rfq_created",
            "project_hydrated",
            "rfq_pending",
            {"rfq_id": rfq.id, "items": len(rfq_parts)},
        )

    db.flush()
    db.refresh(rfq)
    logger.info("RFQ created: %s with %d items (of %d total parts)", rfq.id, len(rfq_parts), len(parts))
    return rfq


def get_rfq(db: Session, rfq_id: str) -> Optional[RFQBatch]:
    return db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()


def get_user_rfqs(db: Session, user_id: str) -> List[RFQBatch]:
    return db.query(RFQBatch).filter(RFQBatch.requested_by_user_id == user_id).order_by(RFQBatch.created_at.desc()).all()


def update_rfq_status(db: Session, rfq_id: str, status: str) -> Optional[RFQBatch]:
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if rfq:
        old_status = rfq.status
        rfq.status = status

        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first() if rfq.bom_id else None
        if project:
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

            project.workflow_stage = normalized_stage
            project.status = normalized_stage
            project.rfq_status = normalized_rfq
            project.project_metadata = project.project_metadata or {}
            project.project_metadata["workflow_stage"] = normalized_stage
            project.project_metadata["rfq_status"] = normalized_rfq
            project.project_metadata["next_action"] = project_service.project_stage_action(normalized_stage)

            project_service.record_project_event(
                db,
                project,
                "rfq_status_updated",
                old_status,
                normalized_stage,
                {"rfq_id": rfq.id, "rfq_status": status},
            )

        db.flush()
        db.refresh(rfq)
    return rfq


def add_quote_to_rfq(db, rfq_id, item_quotes, vendor_id=None):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    items = db.query(RFQItem).filter(RFQItem.rfq_batch_id == rfq_id).all()
    total = 0.0

    for item in items:
        for quote in item_quotes:
            if quote.get("part_name") == item.part_key:
                item.quoted_price = quote.get("price", 0)
                item.lead_time = quote.get("lead_time", 14)
                total += (item.quoted_price or 0) * (int(item.requested_quantity) or 1)
                break

        rfq.total_final_cost = round(total, 2)
    rfq.selected_vendor_id = vendor_id
    rfq.status = "quoted"

    project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first() if rfq.bom_id else None
    if project:
        project.workflow_stage = "quote_compare"
        project.status = "quote_compare"
        project.rfq_status = "quoted"
        project.project_metadata = project.project_metadata or {}
        project.project_metadata["workflow_stage"] = "quote_compare"
        project.project_metadata["rfq_status"] = "quoted"
        project.project_metadata["next_action"] = "Compare quotes"
        project_service.record_project_event(
            db,
            project,
            "quote_received",
            "rfq_sent",
            "quote_compare",
            {"rfq_id": rfq.id, "vendor_id": vendor_id, "total_final_cost": rfq.total_final_cost},
        )

    db.flush()
    db.refresh(rfq)
    return rfq