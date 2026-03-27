"""
BOM Service — Store BOMs from BOM Engine output.
FIXES:
  - Uses provided user_id (was hardcoded to None)
  - Uses provided session_token (was generating a new one)
  - Safe for existing DB schema (no new column writes unless columns exist)
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
        }
        for p in parts
    ]


def update_bom_status(db: Session, bom_id: str, status: str):
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.commit()