"""
BOM Service v3 — Store BOMs from BOM Engine output.

REMOVED: parse_csv_content(), parse_bom_rows() — duplicate parsing logic.
BOM Engine is the SINGLE SOURCE OF TRUTH for parsing + normalization.

Platform API receives pre-parsed, normalized, classified components
from BOM Engine and stores them directly in PostgreSQL.
"""

import uuid
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.bom import BOM, BOMPart, BOMStatus

logger = logging.getLogger("bom_service")


def create_bom_from_analyzer(
    db: Session,
    analyzer_output: Dict[str, Any],
    file_name: str = "",
    file_type: str = "csv",
    user_id: Optional[str] = None,
) -> BOM:
    """
    Create BOM + BOMPart records from BOM Engine's normalized output.

    No local parsing — BOM Engine handles all:
      - CSV/XLSX parsing
      - Column detection (UBNE)
      - Text normalization
      - Classification
      - Spec extraction

    Args:
        db: Database session
        analyzer_output: Full response from BOM Engine v3
            { "components": [...], "summary": {...}, "_meta": {...} }
        file_name: Original filename
        file_type: File extension (csv, xlsx)
        user_id: Optional authenticated user ID
    """
    components = analyzer_output.get("components", [])
    session_token = uuid.uuid4().hex

    bom = BOM(
        user_id=None,
        session_token=session_token,
        name=file_name or "Uploaded BOM",
        file_name=file_name,
        file_type=file_type,
        raw_data=components,  # store full normalized+classified data
        total_parts=len(components),
        status=BOMStatus.uploaded.value,
    )
    db.add(bom)
    db.flush()

    for comp in components:
        db.add(BOMPart(
            bom_id=bom.id,
            part_name=comp.get("description") or comp.get("standard_text", ""),
            material=comp.get("material", ""),
            quantity=max(1, int(comp.get("quantity", 1))),
            manufacturer=comp.get("manufacturer", ""),
            mpn=comp.get("mpn", ""),
            category=comp.get("category", ""),
            notes=comp.get("notes", ""),
            specs=comp.get("specs", {}),
            geometry_type=comp.get("geometry"),
        ))

    db.commit()
    db.refresh(bom)
    logger.info(
        f"BOM created: {bom.id} with {len(components)} parts "
        f"(categories: {analyzer_output.get('summary', {}).get('categories', {})})"
    )
    return bom


def get_bom(db: Session, bom_id: str) -> Optional[BOM]:
    """Get a BOM by ID."""
    return db.query(BOM).filter(BOM.id == bom_id).first()


def get_bom_parts_as_dicts(db: Session, bom_id: str) -> List[Dict[str, Any]]:
    """Get BOM parts as list of dicts for downstream services."""
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).all()
    return [
        {
            "part_name": p.part_name or "",
            "quantity": p.quantity or 1,
            "material": p.material or "",
            "manufacturer": p.manufacturer or "",
            "mpn": p.mpn or "",
            "notes": p.notes or "",
            "category": p.category or "",
            "specs": p.specs or {},
        }
        for p in parts
    ]


def update_bom_status(db: Session, bom_id: str, status: str):
    """Update BOM status."""
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.commit()
