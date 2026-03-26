"""RFQ Service — FIXED: only quote-required parts, correct currency, bom_part linkage."""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.models.rfq import RFQ, RFQItem, RFQStatus
from app.models.bom import BOM, BOMPart
from app.models.analysis import AnalysisResult
from app.models.project import Project

logger = logging.getLogger("rfq_service")


def create_rfq_from_analysis(
    db: Session,
    bom_or_project_id: str,
    user_id: Optional[str] = None,
    notes: str = "",
) -> RFQ:
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

    # FIXED: Use project currency instead of hardcoded USD
    currency = "USD"
    if project and project.currency:
        currency = project.currency
    elif project and project.analyzer_report:
        currency = project.analyzer_report.get("section_1_executive_summary", {}).get("currency", "USD")

    rfq = RFQ(
        user_id=user_id or bom.user_id,
        bom_id=bom.id,
        project_id=project.id if project else None,
        status=RFQStatus.created.value,
        total_estimated_cost=analysis.average_cost if analysis else None,
        currency=currency,  # FIXED: was hardcoded "USD"
        notes=notes or "Auto-generated from BOM analysis",
    )
    db.add(rfq)
    db.flush()

    # FIXED: Only include parts that require quotation, not ALL parts
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom.id).all()
    rfq_parts = [
        p for p in parts
        if p.rfq_required
        or p.category in ("custom_mechanical", "sheet_metal", "custom", "raw_material")
        or p.procurement_class == "rfq_required"
    ]

    if not rfq_parts:
        # Fallback: if no parts are marked rfq_required, include custom/unknown
        rfq_parts = [p for p in parts if p.category in ("custom", "custom_mechanical", "sheet_metal", "unknown")]

    for p in rfq_parts:
        db.add(RFQItem(
            rfq_id=rfq.id,
            bom_part_id=p.id,       # FIXED: was missing — now links to BOMPart
            part_name=p.part_name,
            quantity=p.quantity,
            material=p.material,
            drawing_required=p.drawing_required or False,
        ))

    if project:
        project.rfq_status = rfq.status
        project.status = "quoting"

    db.flush()
    db.refresh(rfq)
    logger.info("RFQ created: %s with %d items (of %d total parts)", rfq.id, len(rfq_parts), len(parts))
    return rfq


def get_rfq(db: Session, rfq_id: str) -> Optional[RFQ]:
    return db.query(RFQ).filter(RFQ.id == rfq_id).first()


def get_user_rfqs(db: Session, user_id: str) -> List[RFQ]:
    return db.query(RFQ).filter(RFQ.user_id == user_id).order_by(RFQ.created_at.desc()).all()


def update_rfq_status(db: Session, rfq_id: str, status: str) -> Optional[RFQ]:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if rfq:
        rfq.status = status
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first() if rfq.bom_id else None
        if project:
            if status == RFQStatus.quoted.value:
                project.status = "quoted"
            elif status == RFQStatus.approved.value:
                project.status = "approved"
            elif status == RFQStatus.rejected.value:
                project.status = "analyzed"
            elif status == RFQStatus.in_production.value:
                project.status = "in_production"
            elif status == RFQStatus.completed.value:
                project.status = "completed"
            project.rfq_status = status
        db.flush()
        db.refresh(rfq)
    return rfq


def add_quote_to_rfq(
    db: Session,
    rfq_id: str,
    item_quotes: List[Dict[str, Any]],
    vendor_id: Optional[str] = None,
) -> RFQ:
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    items = db.query(RFQItem).filter(RFQItem.rfq_id == rfq_id).all()
    total = 0.0

    for item in items:
        for quote in item_quotes:
            if quote.get("part_name") == item.part_name:
                item.quoted_price = quote.get("price", 0)
                item.lead_time = quote.get("lead_time", 14)
                total += (item.quoted_price or 0) * (item.quantity or 1)
                break

    rfq.total_final_cost = round(total, 2)
    rfq.selected_vendor_id = vendor_id
    rfq.status = RFQStatus.quoted.value

    project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first() if rfq.bom_id else None
    if project:
        project.status = "quoted"
        project.rfq_status = RFQStatus.quoted.value

    db.flush()
    db.refresh(rfq)
    return rfq