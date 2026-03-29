"""Drawing Service — durable storage for custom part technical drawings.
Maps to sourcing.drawing_assets in PostgreSQL.

Storage providers:
  - 's3': AWS S3 / compatible (Railway Object Storage, MinIO)
  - 'local': Local filesystem (ephemeral on Railway — dev only)

Set DRAWING_STORAGE_PROVIDER=s3 and provide:
  DRAWING_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
"""
import os
import uuid
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Tuple

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

STORAGE_PROVIDER = os.getenv("DRAWING_STORAGE_PROVIDER", "local")
S3_BUCKET = os.getenv("DRAWING_S3_BUCKET", "")
S3_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_PREFIX = os.getenv("DRAWING_S3_PREFIX", "drawings/")

_production_checked = False


def _enforce_production_storage():
    """Fail fast if production lacks durable storage config."""
    global _production_checked
    if _production_checked:
        return
    _production_checked = True
    try:
        from app.core.config import settings
        if settings.is_production and STORAGE_PROVIDER == "local":
            logger.error(
                "FATAL: ENVIRONMENT=production but DRAWING_STORAGE_PROVIDER=local. "
                "Drawings on ephemeral disk WILL be lost. Set DRAWING_STORAGE_PROVIDER=s3 "
                "and configure DRAWING_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY."
            )
            raise RuntimeError("Production requires S3 drawing storage. Set DRAWING_STORAGE_PROVIDER=s3.")
        if STORAGE_PROVIDER == "s3" and not S3_BUCKET:
            logger.error("DRAWING_STORAGE_PROVIDER=s3 but DRAWING_S3_BUCKET is empty.")
            raise RuntimeError("S3 storage configured but DRAWING_S3_BUCKET is empty.")
        if STORAGE_PROVIDER == "local":
            logger.warning(
                "Drawing storage: LOCAL (dev-only, ephemeral). "
                "Set DRAWING_STORAGE_PROVIDER=s3 for production."
            )
    except ImportError:
        pass


# ── Storage backends ──

def _get_s3_client():
    """Lazy-init boto3 client."""
    try:
        import boto3
        return boto3.client("s3", region_name=S3_REGION)
    except ImportError:
        logger.error("boto3 not installed — falling back to local storage")
        return None


def _save_to_s3(file_bytes: bytes, key: str) -> str:
    client = _get_s3_client()
    if not client or not S3_BUCKET:
        raise RuntimeError("S3 not configured")
    client.put_object(Bucket=S3_BUCKET, Key=key, Body=file_bytes)
    logger.info(f"Saved drawing to S3: s3://{S3_BUCKET}/{key}")
    return key


def _get_from_s3(key: str) -> Optional[bytes]:
    client = _get_s3_client()
    if not client or not S3_BUCKET:
        return None
    try:
        response = client.get_object(Bucket=S3_BUCKET, Key=key)
        return response["Body"].read()
    except Exception as e:
        logger.error(f"S3 download failed for {key}: {e}")
        return None


def _get_local_root() -> Path:
    root = Path(settings.UPLOAD_DIR) / "drawings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save_to_local(file_bytes: bytes, rfq_id: str, safe_name: str) -> str:
    rfq_dir = _get_local_root() / rfq_id
    rfq_dir.mkdir(parents=True, exist_ok=True)
    storage_path = rfq_dir / safe_name
    storage_path.write_bytes(file_bytes)
    return str(storage_path)


def _get_from_local(path: str) -> Optional[bytes]:
    p = Path(path)
    if not p.exists():
        logger.error("Drawing file missing from disk: %s", path)
        return None
    return p.read_bytes()


def _validate_file(filename: str, size_bytes: int) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    if size_bytes > MAX_DRAWING_SIZE_MB * 1024 * 1024:
        raise ValueError(f"File too large ({size_bytes / 1e6:.1f} MB). Max {MAX_DRAWING_SIZE_MB} MB.")
    return ext


# ── Public API ──

def save_drawing(db, rfq_id, user_id, file_bytes, original_filename,
                 part_name="", part_notes="", rfq_item_id=None, bom_id=None):
    _enforce_production_storage()

    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise ValueError(f"RFQ not found: {rfq_id}")

    ext = _validate_file(original_filename, len(file_bytes))
    safe_name = f"{uuid.uuid4().hex}{ext}"
    mime = MIME_MAP.get(ext.lstrip("."), "application/octet-stream")
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    provider = STORAGE_PROVIDER
    if provider == "s3" and S3_BUCKET:
        try:
            s3_key = f"{S3_PREFIX}{rfq_id}/{safe_name}"
            storage_path = _save_to_s3(file_bytes, s3_key)
            provider = "s3"
        except Exception as e:
            logger.warning(f"S3 save failed ({e}), falling back to local")
            storage_path = _save_to_local(file_bytes, rfq_id, safe_name)
            provider = "local"
    else:
        storage_path = _save_to_local(file_bytes, rfq_id, safe_name)
        provider = "local"

    drawing = DrawingAsset(
        bom_id=bom_id or rfq.bom_id,
        bom_part_id=None,
        rfq_item_id=rfq_item_id,
        rfq_batch_id=rfq_id,
        project_id=rfq.project_id,
        storage_provider=provider,
        storage_path=storage_path,
        file_name=original_filename[:500],
        mime_type=mime,
        file_size_bytes=len(file_bytes),
        file_hash=file_hash,
        is_primary=False,
        created_by_user_id=user_id,
    )
    drawing._extra = {"part_name": part_name, "part_notes": part_notes, "status": "received"}
    db.add(drawing)
    db.flush()
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

    provider = drawing.storage_provider or "local"
    if provider == "s3":
        file_bytes = _get_from_s3(drawing.storage_path)
    else:
        file_bytes = _get_from_local(drawing.storage_path)

    if not file_bytes:
        return None
    mime = drawing.mime_type or "application/octet-stream"
    return file_bytes, drawing.file_name, mime
