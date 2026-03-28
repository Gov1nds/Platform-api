"""
BOM Service — Store BOMs from BOM Engine output.
Updated for PostgreSQL schema (bom.boms, bom.bom_parts).
"""
import uuid
import hashlib
import json
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.bom import BOM, BOMPart
from app.models.user import GuestSession

logger = logging.getLogger("bom_service")

_CUSTOM_CATEGORIES = {"custom_mechanical", "sheet_metal", "custom"}


def _row_hash(comp: Dict[str, Any]) -> str:
    """Deterministic hash of a component's identity fields for dedup tracking."""
    key_fields = {
        "item_id": comp.get("item_id", ""),
        "description": comp.get("description", ""),
        "mpn": comp.get("mpn", ""),
        "manufacturer": comp.get("manufacturer", ""),
        "material": comp.get("material", ""),
        "quantity": comp.get("quantity", 1),
    }
    return hashlib.sha256(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()[:16]


def _is_custom_part(comp: Dict[str, Any]) -> bool:
    category = (comp.get("category") or "").lower()
    if category in _CUSTOM_CATEGORIES:
        return True
    if comp.get("is_custom", False):
        return True
    if (comp.get("procurement_class") or "").lower() == "rfq_required":
        return True
    return False


def _ensure_guest_session(db: Session, session_token: str) -> Optional[str]:
    """Create or fetch guest session, return its ID."""
    if not session_token:
        return None
    gs = db.query(GuestSession).filter(GuestSession.session_token == session_token).first()
    if gs:
        return gs.id
    gs = GuestSession(session_token=session_token)
    db.add(gs)
    db.flush()
    return gs.id


def create_bom_from_analyzer(
    db: Session,
    analyzer_output: Dict[str, Any],
    file_name: str = "",
    file_type: str = "csv",
    user_id: Optional[str] = None,
    session_token: Optional[str] = None,
) -> BOM:
    components = analyzer_output.get("components", [])

    guest_session_id = None
    if not user_id and session_token:
        guest_session_id = _ensure_guest_session(db, session_token)

    custom_count = sum(1 for c in components if _is_custom_part(c))
    raw_count = sum(1 for c in components if (c.get("category") or "").lower() == "raw_material")
    standard_count = len(components) - custom_count - raw_count
    meta = analyzer_output.get("_meta", {})

    bom = BOM(
        uploaded_by_user_id=user_id,
        guest_session_id=guest_session_id,
        source_file_name=file_name or "upload.csv",
        source_file_type=file_type or "csv",
        source_checksum=meta.get("file_checksum"),
        name=file_name or "Uploaded BOM",
        raw_payload=components,
        total_parts=len(components),
        total_custom_parts=custom_count,
        total_standard_parts=standard_count,
        total_raw_parts=raw_count,
        status="uploaded",
    )
    # Cache session_token for backward compat property
    bom._session_token_cache = session_token or uuid.uuid4().hex
    db.add(bom)
    db.flush()

    for idx, comp in enumerate(components):
        custom = _is_custom_part(comp)
        category_code = comp.get("category", "unknown")
        procurement_class = comp.get("procurement_class", "catalog_purchase")
        rfq_required = comp.get("rfq_required", False)
        drawing_required = comp.get("drawing_required", False)

        if custom:
            rfq_required = True
            drawing_required = True
            if procurement_class == "catalog_purchase":
                procurement_class = "rfq_required"

        # Map to valid procurement_class values from bootstrap schema
        valid_classes = {
            "catalog_purchase", "rfq_required", "raw_material", "custom_manufacture",
            "engineering_review", "electrical_part", "electronics_part", "sheet_metal",
            "machined_part", "unknown"
        }
        if procurement_class not in valid_classes:
            procurement_class = "custom_manufacture" if custom else "catalog_purchase"

        db.add(BOMPart(
            bom_id=bom.id,
            item_id=comp.get("item_id", str(idx + 1)),
            raw_text=comp.get("raw_text", ""),
            normalized_text=comp.get("standard_text", ""),
            canonical_name=comp.get("description") or comp.get("standard_text", ""),
            description=comp.get("description", ""),
            quantity=max(1, int(comp.get("quantity", 1))),
            part_number=comp.get("part_number", ""),
            mpn=comp.get("mpn", ""),
            manufacturer=comp.get("manufacturer", ""),
            category_code=category_code,
            procurement_class=procurement_class,
            material=comp.get("material", ""),
            material_form=comp.get("material_form"),
            geometry=comp.get("geometry"),
            tolerance=comp.get("tolerance"),
            secondary_ops=comp.get("secondary_ops", []),
            specs=comp.get("specs", {}),
            classification_confidence=comp.get("classification_confidence", 0),
            classification_reason=comp.get("classification_reason", ""),
            has_mpn=comp.get("has_mpn", False),
            has_brand=comp.get("has_brand", False),
            is_generic=comp.get("is_generic", False),
            is_raw=comp.get("is_raw", False),
            is_custom=custom,
            rfq_required=rfq_required,
            drawing_required=drawing_required,
            source_row=idx + 1,
            source_row_hash=_row_hash(comp),
            canonical_part_key=comp.get("canonical_part_key", ""),
        ))

    # FIXED: flush instead of commit — let the route handler own the transaction
    db.flush()
    db.refresh(bom)
    logger.info(
        "BOM created: %s | user=%s | %d parts (%d custom, %d standard)",
        bom.id, user_id, len(components), custom_count, standard_count,
    )
    return bom


def get_bom(db: Session, bom_id: str) -> Optional[BOM]:
    return db.query(BOM).filter(BOM.id == bom_id).first()


def get_bom_parts_as_dicts(db: Session, bom_id: str) -> List[Dict[str, Any]]:
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).all()
    return [
        {
            "part_name": p.canonical_name or p.description or "",
            "quantity": int(p.quantity) if p.quantity else 1,
            "material": p.material or "",
            "manufacturer": p.manufacturer or "",
            "mpn": p.mpn or "",
            "notes": p.raw_text or "",
            "category": p.category_code or "",
            "specs": p.specs or {},
            "is_custom": p.is_custom or False,
            "part_type": "custom" if p.is_custom else "standard",
            "rfq_required": p.rfq_required or False,
            "drawing_required": p.drawing_required or False,
            "procurement_class": p.procurement_class or "catalog_purchase",
            "canonical_part_key": p.canonical_part_key or "",
        }
        for p in parts
    ]


def update_bom_status(db: Session, bom_id: str, status: str):
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if bom:
        bom.status = status
        db.flush()