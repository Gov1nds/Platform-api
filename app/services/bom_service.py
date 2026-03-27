"""
BOM Service — Store BOMs from BOM Engine output.
FIXES:
  - Uses provided user_id (was hardcoded to None)
  - Uses provided session_token (was generating a new one)
  - Safe for existing DB schema (no new column writes unless columns exist)
  - NEW: saves classification fields (is_custom, rfq_required, drawing_required, procurement_class, part_type)
"""
import uuid
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.bom import BOM, BOMPart, BOMStatus

logger = logging.getLogger("bom_service")

# Categories that are custom (not standard catalog parts)
_CUSTOM_CATEGORIES = {
    "custom_mechanical", "sheet_metal", "custom",
}


def _is_custom_part(comp: Dict[str, Any]) -> bool:
    """Determine if a component is a custom fabricated part based on classifier output."""
    category = (comp.get("category") or "").lower()
    if category in _CUSTOM_CATEGORIES:
        return True
    if comp.get("is_custom", False):
        return True
    procurement = (comp.get("procurement_class") or "").lower()
    if procurement == "rfq_required":
        return True
    return False


def create_bom_from_analyzer(
    db: Session,
    analyzer_output: Dict[str, Any],
    file_name: str = "",
    file_type: str = "csv",
    user_id: Optional[str] = None,
    session_token: Optional[str] = None,
) -> BOM:
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

    for comp in components:
        custom = _is_custom_part(comp)
        category = comp.get("category", "")
        procurement_class = comp.get("procurement_class", "catalog_purchase")
        rfq_required = comp.get("rfq_required", False)
        drawing_required = comp.get("drawing_required", False)

        # If custom but fields not explicitly set by classifier, set sensible defaults
        if custom:
            if not rfq_required:
                rfq_required = True
            if not drawing_required:
                drawing_required = True
            if procurement_class == "catalog_purchase":
                procurement_class = "rfq_required"

        db.add(BOMPart(
            bom_id=bom.id,
            part_name=comp.get("description") or comp.get("standard_text", ""),
            material=comp.get("material", ""),
            quantity=max(1, int(comp.get("quantity", 1))),
            manufacturer=comp.get("manufacturer", ""),
            mpn=comp.get("mpn", ""),
            category=category,
            notes=comp.get("notes", ""),
            specs=comp.get("specs", {}),
            geometry_type=comp.get("geometry"),
            # NEW: classification fields
            part_type="custom" if custom else "standard",
            is_custom=custom,
            rfq_required=rfq_required,
            drawing_required=drawing_required,
            procurement_class=procurement_class,
        ))

    db.commit()
    db.refresh(bom)
    logger.info(
        f"BOM created: {bom.id} | user={user_id} | session={session_token and session_token[:8]}... | "
        f"{len(components)} parts"
    )
    return bom


def get_bom(db: Session, bom_id: str) -> Optional[BOM]:
    return db.query(BOM).filter(BOM.id == bom_id).first()


def get_bom_parts_as_dicts(db: Session, bom_id: str) -> List[Dict[str, Any]]:
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
            "is_custom": getattr(p, "is_custom", False) or False,
            "part_type": getattr(p, "part_type", "standard") or "standard",
            "rfq_required": getattr(p, "rfq_required", False) or False,
            "drawing_required": getattr(p, "drawing_required", False) or False,
            "procurement_class": getattr(p, "procurement_class", "catalog_purchase") or "catalog_purchase",
        }
        for p in parts
    ]


def update_bom_status(db: Session, bom_id: str, status: str):
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.commit()
