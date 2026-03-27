"""Drawing Service — file storage for custom part technical drawings.
Maps to sourcing.drawing_assets in PostgreSQL.
"""
import uuid
import logging
from pathlib import Path
from typing import Optional, List

from sqlalchemy.orm import Session

from app.models.drawing import DrawingAsset
from app.models.rfq import RFQBatch
from app.core.config import settings

logger = logging.getLogger("drawing_service")

ALLOWED_EXTENSIONS = {
    ".pdf", ".dxf", ".step", ".stp", ".dwg", ".stl",
    ".iges", ".igs", ".png", ".jpg", ".jpeg"
}
MAX_DRAWING_SIZE_MB = 50

MIME_MAP = {
    "pdf": "application/pdf", "dxf": "application/dxf",
    "step": "application/step", "stp": "application/step",
    "dwg": "image/vnd.dwg", "stl": "model/stl",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "iges": "model/iges", "igs": "model/iges",
}


def _get_storage_root() -> Path:
    root = Path(settings.UPLOAD_DIR) / "drawings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_file(filename: str, size_bytes: int) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    if size_bytes > MAX_DRAWING_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File too large ({size_bytes / 1e6:.1f} MB). Max {MAX_DRAWING_SIZE_MB} MB.")
    return ext


def save_drawing(db, rfq_id, user_id, file_bytes, original_filename,
                 part_name="", part_notes="", rfq_item_id=None, bom_id=None):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    ext = _validate_file(original_filename, len(file_bytes))
    safe_name = f"{uuid.uuid4().hex}{ext}"
    rfq_dir = _get_storage_root() / rfq_id
    rfq_dir.mkdir(parents=True, exist_ok=True)
    storage_path = rfq_dir / safe_name
    storage_path.write_bytes(file_bytes)

    mime = MIME_MAP.get(ext.lstrip("."), "application/octet-stream")

    drawing = DrawingAsset(
        bom_id=bom_id or rfq.bom_id,
        bom_part_id=None,
        rfq_item_id=rfq_item_id,
        rfq_batch_id=rfq_id,
        project_id=rfq.project_id,
        storage_provider="local",
        storage_path=str(storage_path),
        file_name=original_filename[:500],
        mime_type=mime,
        file_size_bytes=len(file_bytes),
        is_primary=False,
        created_by_user_id=user_id,
    )
    # Store extra fields in a transient attribute
    drawing._extra = {"part_name": part_name, "part_notes": part_notes, "status": "received"}
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return drawing


def get_drawings_for_rfq(db, rfq_id):
    return (
        db.query(DrawingAsset)
        .filter(DrawingAsset.rfq_batch_id == rfq_id)
        .order_by(DrawingAsset.created_at.desc())
        .all()
    )


def get_drawing_file(db, drawing_id):
    drawing = db.query(DrawingAsset).filter(DrawingAsset.id == drawing_id).first()
    if not drawing or not drawing.storage_path:
        return None
    path = Path(drawing.storage_path)
    if not path.exists():
        logger.error("Drawing file missing from disk: %s", drawing.storage_path)
        return None
    mime = drawing.mime_type or "application/octet-stream"
    return path.read_bytes(), drawing.file_name, mime
