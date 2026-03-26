"""
BOM Service v4 — Store BOMs from BOM Engine output.

FIXES:
  - Uses provided user_id (was hardcoded to None)
  - Uses provided session_token (was generating a new one)
  - Gracefully handles missing new columns (classification_confidence etc.)
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
    session_token: Optional[str] = None,
) -> BOM:
    """
    Create BOM + BOMPart records from BOM Engine's normalized output.

    FIXED: Now actually uses user_id and session_token parameters.
    """
    components = analyzer_output.get("components", [])

    bom = BOM(
        user_id=user_id,                                    # FIXED: was None
        session_token=session_token or uuid.uuid4().hex,    # FIXED: uses provided token
        name=file_name or "Uploaded BOM",
        file_name=file_name,
        file_type=file_type,
        raw_data=components,
        total_parts=len(components),
        status=BOMStatus.uploaded.value,
    )
    db.add(bom)
    db.flush()

    # Check if new columns exist by inspecting the model
    _has_new_cols = hasattr(BOMPart, 'classification_confidence') and \
                    _column_exists(db, 'bom_parts', 'classification_confidence')

    for comp in components:
        part_kwargs = dict(
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
        )
        # Only set new columns if the DB schema supports them
        if _has_new_cols:
            part_kwargs["classification_confidence"] = comp.get("classification_confidence", 0.0)
            part_kwargs["procurement_class"] = comp.get("procurement_class", "catalog_purchase")
            part_kwargs["rfq_required"] = comp.get("rfq_required", False)
            part_kwargs["drawing_required"] = comp.get("drawing_required", False)

        db.add(BOMPart(**part_kwargs))

    db.commit()
    db.refresh(bom)
    logger.info(
        f"BOM created: {bom.id} | user_id={user_id} | session={session_token and session_token[:8]}... | "
        f"{len(components)} parts (categories: {analyzer_output.get('summary', {}).get('categories', {})})"
    )
    return bom


def _column_exists(db: Session, table: str, column: str) -> bool:
    """Check if a column exists in the database table."""
    try:
        from sqlalchemy import text
        # Works for PostgreSQL
        result = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ), {"table": table, "column": column})
        return result.fetchone() is not None
    except Exception:
        # If the check itself fails, assume columns don't exist
        return False


def get_bom(db: Session, bom_id: str) -> Optional[BOM]:
    return db.query(BOM).filter(BOM.id == bom_id).first()


def get_bom_parts_as_dicts(db: Session, bom_id: str) -> List[Dict[str, Any]]:
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).all()
    result = []
    for p in parts:
        d = {
            "part_name": p.part_name or "",
            "quantity": p.quantity or 1,
            "material": p.material or "",
            "manufacturer": p.manufacturer or "",
            "mpn": p.mpn or "",
            "notes": p.notes or "",
            "category": p.category or "",
            "specs": p.specs or {},
        }
        # Include new fields if they exist
        if hasattr(p, 'procurement_class') and p.procurement_class is not None:
            d["procurement_class"] = p.procurement_class
            d["rfq_required"] = p.rfq_required or False
            d["drawing_required"] = p.drawing_required or False
        else:
            d["procurement_class"] = "catalog_purchase"
            d["rfq_required"] = False
            d["drawing_required"] = False
        result.append(d)
    return result


def update_bom_status(db: Session, bom_id: str, status: str):
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.commit()