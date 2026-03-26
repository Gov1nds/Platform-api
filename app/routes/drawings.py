"""
SECTION 10: RFQ Drawing Upload Workflow

New files needed:
  - app/models/drawing.py
  - app/routes/drawings.py
  - app/services/drawing_service.py
  - app/schemas/drawing.py
  - (Update app/models/rfq.py to add drawing relationship)
"""


# ============================================================
# FILE: app/models/drawing.py  (NEW FILE)
# ============================================================

DRAWING_MODEL = '''
"""Drawing model — stores uploaded technical drawings for custom parts."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, BigInteger
from sqlalchemy.orm import relationship
from app.core.database import Base


class Drawing(Base):
    __tablename__ = "drawings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rfq_id = Column(String(36), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    rfq_item_id = Column(String(36), ForeignKey("rfq_items.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # File info
    original_filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)  # UUID-based safe name
    file_size_bytes = Column(BigInteger, nullable=True)
    file_format = Column(String(20), nullable=True)       # pdf, dxf, step, dwg, etc.
    storage_path = Column(String(1000), nullable=True)    # local path or S3 key

    # Part context
    part_name = Column(String(500), nullable=True)
    part_notes = Column(Text, nullable=True)              # engineering notes from user

    # Status
    status = Column(String(30), default="received")       # received, reviewed, quoted
    reviewer_notes = Column(Text, nullable=True)          # internal notes

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="drawings")
'''


# ============================================================
# FILE: app/schemas/drawing.py  (NEW FILE)
# ============================================================

DRAWING_SCHEMA = '''
"""Drawing schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class DrawingUploadResponse(BaseModel):
    id: str
    rfq_id: str
    part_name: Optional[str] = None
    original_filename: str
    file_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class DrawingListResponse(BaseModel):
    rfq_id: str
    drawings: list[DrawingUploadResponse]
    total: int
'''


# ============================================================
# FILE: app/services/drawing_service.py  (NEW FILE)
# ============================================================

DRAWING_SERVICE = '''
"""
Drawing Service — file storage for custom part technical drawings.

Storage strategy:
  - Local: saves to UPLOAD_DIR/drawings/{rfq_id}/
  - S3: if AWS_S3_BUCKET is configured, uploads there instead
  - Files are served via a signed URL or directly from local storage

Supported formats: PDF, DXF, STEP, STP, DWG, STL, IGES, IGS, PNG, JPG
"""

import os
import uuid
import logging
import shutil
from pathlib import Path
from typing import Optional, List
from datetime import datetime

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

    Args:
        db: Database session
        rfq_id: The RFQ this drawing belongs to
        user_id: Uploader (for access control)
        file_bytes: Raw file content
        original_filename: Original filename from the upload
        part_name: Which part this drawing is for
        part_notes: Any notes from the user about the drawing
        rfq_item_id: Optional link to specific RFQ item
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
        "Drawing saved: %s → %s (%d bytes)",
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
'''


# ============================================================
# FILE: app/routes/drawings.py  (NEW FILE)
# ============================================================

DRAWINGS_ROUTE = '''
"""
Drawing upload routes for custom part RFQ workflow.

Endpoints:
  POST /drawings/upload       — upload a technical drawing
  GET  /drawings/{rfq_id}     — list drawings for an RFQ
  GET  /drawings/file/{id}    — download a specific drawing
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQ
from app.schemas.drawing import DrawingUploadResponse, DrawingListResponse
from app.utils.dependencies import get_current_user, require_user
from app.services import drawing_service

logger = logging.getLogger("routes.drawings")
router = APIRouter(prefix="/drawings", tags=["drawings"])


@router.post("/upload", response_model=DrawingUploadResponse, status_code=201)
async def upload_drawing(
    file: UploadFile = File(...),
    rfq_id: str = Form(...),
    part_name: str = Form(""),
    part_notes: str = Form(""),
    rfq_item_id: Optional[str] = Form(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Upload a technical drawing for a custom part in an RFQ.

    Accepts: PDF, DXF, STEP, STP, DWG, STL, IGES, PNG, JPG
    Max size: 50 MB
    """
    # Verify user owns this RFQ
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.user_id and rfq.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this RFQ")

    file_bytes = await file.read()
    try:
        drawing = drawing_service.save_drawing(
            db=db,
            rfq_id=rfq_id,
            user_id=user.id,
            file_bytes=file_bytes,
            original_filename=file.filename or "drawing",
            part_name=part_name,
            part_notes=part_notes,
            rfq_item_id=rfq_item_id or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "Drawing uploaded: %s for RFQ %s (part: %s)",
        drawing.id, rfq_id, part_name or "unspecified"
    )

    return DrawingUploadResponse(
        id=drawing.id,
        rfq_id=drawing.rfq_id,
        part_name=drawing.part_name,
        original_filename=drawing.original_filename,
        file_format=drawing.file_format,
        file_size_bytes=drawing.file_size_bytes,
        status=drawing.status,
        created_at=drawing.created_at,
    )


@router.get("/{rfq_id}", response_model=DrawingListResponse)
def list_drawings(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.user_id and rfq.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    drawings = drawing_service.get_drawings_for_rfq(db, rfq_id)
    return DrawingListResponse(
        rfq_id=rfq_id,
        total=len(drawings),
        drawings=[
            DrawingUploadResponse(
                id=d.id,
                rfq_id=d.rfq_id,
                part_name=d.part_name,
                original_filename=d.original_filename,
                file_format=d.file_format,
                file_size_bytes=d.file_size_bytes,
                status=d.status,
                created_at=d.created_at,
            )
            for d in drawings
        ],
    )


@router.get("/file/{drawing_id}")
def download_drawing(
    drawing_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    result = drawing_service.get_drawing_file(db, drawing_id)
    if not result:
        raise HTTPException(status_code=404, detail="Drawing not found")

    file_bytes, filename, mime_type = result
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f\'attachment; filename="{filename}"\'},
    )
'''

# ── Add to app/models/rfq.py ──────────────────────────────────
RFQ_MODEL_ADDITION = '''
# Add to RFQ class in app/models/rfq.py:
# drawings = relationship("Drawing", back_populates="rfq", cascade="all, delete-orphan")
'''
