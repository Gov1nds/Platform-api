"""BOM Service — upload, parse, store BOMs."""
import uuid
import csv
import io
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.bom import BOM, BOMPart, BOMStatus

logger = logging.getLogger("bom_service")


def parse_csv_content(content: str) -> List[Dict[str, Any]]:
    """Parse CSV string into list of row dicts."""
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for row in reader:
        if any(str(v).strip() for v in row.values()):
            rows.append({k: str(v).strip() for k, v in row.items()})
    return rows


def parse_bom_rows(raw_rows: List[Dict]) -> List[Dict[str, Any]]:
    """Normalize raw rows into standard part dicts."""
    col_map = {
        "part_name": ["part_name", "part name", "name", "item", "description", "component", "item name"],
        "quantity": ["quantity", "qty", "count", "amount"],
        "material": ["material", "mat", "material_type"],
        "manufacturer": ["manufacturer", "mfr", "make", "brand", "vendor"],
        "mpn": ["mpn", "part_number", "part number", "pn", "mfg_part_number"],
        "notes": ["notes", "note", "remarks", "specification", "spec"],
    }

    def _find_col(row: Dict, aliases: List[str]) -> str:
        for a in aliases:
            for k in row:
                if k.strip().lower() == a:
                    return str(row[k]).strip()
        return ""

    parts = []
    for row in raw_rows:
        pn = _find_col(row, col_map["part_name"])
        if not pn:
            # Use first non-empty value
            for v in row.values():
                if str(v).strip():
                    pn = str(v).strip()
                    break
        if not pn:
            continue

        qty_str = _find_col(row, col_map["quantity"])
        try:
            qty = max(1, int(float(qty_str))) if qty_str else 1
        except (ValueError, TypeError):
            qty = 1

        parts.append({
            "part_name": pn,
            "quantity": qty,
            "material": _find_col(row, col_map["material"]),
            "manufacturer": _find_col(row, col_map["manufacturer"]),
            "mpn": _find_col(row, col_map["mpn"]),
            "notes": _find_col(row, col_map["notes"]),
        })

    return parts


def create_bom(
    db: Session,
    raw_rows: List[Dict],
    file_name: str = "",
    file_type: str = "csv",
    user_id: Optional[str] = None,
) -> BOM:
    """Create BOM + BOMPart records in database."""
    parts = parse_bom_rows(raw_rows)
    session_token = uuid.uuid4().hex

    bom = BOM(
        user_id=user_id,
        session_token=session_token,
        name=file_name or "Uploaded BOM",
        file_name=file_name,
        file_type=file_type,
        raw_data=raw_rows,
        total_parts=len(parts),
        status=BOMStatus.uploaded.value,
    )
    db.add(bom)
    db.flush()

    for p in parts:
        db.add(BOMPart(
            bom_id=bom.id,
            part_name=p["part_name"],
            material=p["material"],
            quantity=p["quantity"],
            manufacturer=p["manufacturer"],
            mpn=p["mpn"],
            notes=p["notes"],
        ))

    db.commit()
    db.refresh(bom)
    logger.info(f"BOM created: {bom.id} with {len(parts)} parts")
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
        }
        for p in parts
    ]


def update_bom_status(db: Session, bom_id: str, status: str):
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.commit()
