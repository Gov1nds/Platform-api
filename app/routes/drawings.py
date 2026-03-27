"""Drawing upload routes for custom part RFQ workflow.
Maps to sourcing.drawing_assets.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.rfq import RFQBatch
from app.schemas.drawing import DrawingUploadResponse, DrawingListResponse
from app.utils.dependencies import require_user
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
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this RFQ")

    file_bytes = await file.read()
    try:
        drawing = drawing_service.save_drawing(
            db=db, rfq_id=rfq_id, user_id=user.id,
            file_bytes=file_bytes,
            original_filename=file.filename or "drawing",
            part_name=part_name, part_notes=part_notes,
            rfq_item_id=rfq_item_id or None,
            bom_id=rfq.bom_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return DrawingUploadResponse(
        id=drawing.id, rfq_id=rfq_id,
        part_name=part_name,
        original_filename=drawing.file_name,
        file_format=drawing.mime_type,
        file_size_bytes=drawing.file_size_bytes,
        status="received",
        created_at=drawing.created_at,
    )


@router.get("/{rfq_id}", response_model=DrawingListResponse)
def list_drawings(
    rfq_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    if rfq.requested_by_user_id and rfq.requested_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    drawings = drawing_service.get_drawings_for_rfq(db, rfq_id)
    return DrawingListResponse(
        rfq_id=rfq_id, total=len(drawings),
        drawings=[
            DrawingUploadResponse(
                id=d.id, rfq_id=rfq_id,
                part_name=d.file_name,
                original_filename=d.file_name,
                file_format=d.mime_type,
                file_size_bytes=d.file_size_bytes,
                status="received",
                created_at=d.created_at,
            ) for d in drawings
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
        content=file_bytes, media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
