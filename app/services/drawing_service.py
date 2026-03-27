"""
Drawing Service — file storage for custom part technical drawings.

Storage strategy:
  - Local: saves to UPLOAD_DIR/drawings/{rfq_id}/
  - Files are served via direct download from local storage

Supported formats: PDF, DXF, STEP, STP, DWG, STL, IGES, IGS, PNG, JPG
"""

import uuid
import logging
from pathlib import Path
from typing import Optional, List

from sqlalchemy.orm import Session

from app.models.drawing import Drawing
from app.models.rfq import RFQ
from app.core.config import settings

logger = logging.getLogger("drawing_service")

ALLOWED_EXTENSIONS = {
    ".pdf", ".dxf", ".step", ".stp", ".dwg", ".stl",
    ".iges", ".igs", ".png", ".jpg", ".jpeg"
}

MAX_DRAWING_SIZE_MB = 50


def _get_storage_root() -> Path:
    """Get the root directory for drawing storage."""
    root = Path(settings.UPLOAD_DIR) / "drawings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_file(filename: str, size_bytes: int) -> str:
    """Returns normalized extension or raises ValueError."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file format: {ext}. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    max_bytes = MAX_DRAWING_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise ValueError(f"File too large ({size_bytes / 1e6:.1f} MB). Max {MAX_DRAWING_SIZE_MB} MB.")
    return ext


def save_drawing(
    db: Session,
    rfq_id: str,
    user_id: Optional[str],
    file_bytes: bytes,
    original_filename: str,
    part_name: str = "",
    part_notes: str = "",
    rfq_item_id: Optional[str] = None,
) -> Drawing:
    """
    Save a drawing file and create a Drawing record.
    """
    # Validate RFQ exists
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    ext = _validate_file(original_filename, len(file_bytes))

    # Generate a safe stored filename
    safe_name = f"{uuid.uuid4().hex}{ext}"
    rfq_dir = _get_storage_root() / rfq_id
    rfq_dir.mkdir(parents=True, exist_ok=True)
    storage_path = rfq_dir / safe_name

    # Save file
    storage_path.write_bytes(file_bytes)
    logger.info(
        "Drawing saved: %s -> %s (%d bytes)",
        original_filename, storage_path, len(file_bytes)
    )

    # Create DB record
    drawing = Drawing(
        rfq_id=rfq_id,
        rfq_item_id=rfq_item_id,
        user_id=user_id,
        original_filename=original_filename[:500],
        stored_filename=safe_name,
        file_size_bytes=len(file_bytes),
        file_format=ext.lstrip("."),
        storage_path=str(storage_path),
        part_name=part_name[:500] if part_name else "",
        part_notes=part_notes[:2000] if part_notes else "",
        status="received",
    )
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return drawing


def get_drawings_for_rfq(db: Session, rfq_id: str) -> List[Drawing]:
    return (
        db.query(Drawing)
        .filter(Drawing.rfq_id == rfq_id)
        .order_by(Drawing.created_at.desc())
        .all()
    )


def get_drawing_file(db: Session, drawing_id: str) -> Optional[tuple]:
    """
    Returns (file_bytes, filename, mime_type) or None if not found.
    """
    drawing = db.query(Drawing).filter(Drawing.id == drawing_id).first()
    if not drawing or not drawing.storage_path:
        return None

    path = Path(drawing.storage_path)
    if not path.exists():
        logger.error("Drawing file missing from disk: %s", drawing.storage_path)
        return None

    mime_map = {
        "pdf": "application/pdf",
        "dxf": "application/dxf",
        "step": "application/step", "stp": "application/step",
        "dwg": "image/vnd.dwg",
        "stl": "model/stl",
        "png": "image/png",
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "iges": "model/iges", "igs": "model/iges",
    }
    mime = mime_map.get(drawing.file_format or "", "application/octet-stream")
    return path.read_bytes(), drawing.original_filename, mime
