"""Universal intake routes — BOM, item, material, text, and voice intake."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, BackgroundTasks, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.intake import (
    IntakeParseResponse,
    IntakeSubmitResponse,
    IntakeSessionListResponse,
    IntakeParseRequest,
    IntakeSubmitRequest,
    IntakeSessionSchema,
    IntakeInputType,
    IntakeIntent,
)
from app.services import intake_service
from app.utils.dependencies import get_current_user, require_user

router = APIRouter(prefix="/intake", tags=["intake"])


def _to_session_schema(session) -> IntakeSessionSchema:
    return IntakeSessionSchema.model_validate(intake_service.serialize_session(session))


@router.get("/sessions", response_model=IntakeSessionListResponse)
def list_intake_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session_token: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total = intake_service.list_sessions(
        db,
        user=user,
        session_token=session_token,
        limit=limit,
        offset=offset,
    )
    return IntakeSessionListResponse(
        items=[_to_session_schema(s) for s in items],
        total=total,
    )


@router.get("/sessions/{session_id}", response_model=IntakeSessionSchema)
def get_intake_session(
    session_id: str,
    user: Optional[User] = Depends(get_current_user),
    session_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    session = intake_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Intake session not found")

    if user and session.user_id and session.user_id != user.id and str(getattr(user, "role", "")).lower() != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    if not user and session_token and session.session_token and session.session_token != session_token:
        raise HTTPException(status_code=403, detail="Not authorized")

    return _to_session_schema(session)


@router.post("/parse", response_model=IntakeParseResponse)
async def parse_intake(
    raw_input_text: str = Form(None),
    input_type: IntakeInputType = Form(IntakeInputType.auto),
    intent: IntakeIntent = Form(IntakeIntent.auto),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    session_token: str = Form(None),
    voice_transcript: str = Form(None),
    source_channel: str = Form("web"),
    metadata_json: str = Form("{}"),
    source_file: Optional[UploadFile] = File(None),
    audio_file: Optional[UploadFile] = File(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        metadata = {}
        if metadata_json:
            import json
            metadata = json.loads(metadata_json)

        source_file_path = source_file_size = source_file_name = source_file_type = None
        source_bytes = None
        if source_file:
            source_bytes = await source_file.read()
            source_file_path, source_file_size, source_file_name, source_file_type = intake_service._save_upload_file(source_file, "intake", source_bytes)
            # We already consumed the UploadFile stream; keep the captured bytes.
            source_bytes = source_bytes or b""

        audio_file_path = audio_file_size = audio_file_name = audio_file_type = None
        audio_bytes = None
        if audio_file:
            audio_bytes = await audio_file.read()
            audio_file_path, audio_file_size, audio_file_name, audio_file_type = intake_service._save_upload_file(audio_file, "intake_audio", audio_bytes)
            audio_bytes = audio_bytes or b""

        payload = IntakeParseRequest(
            raw_input_text=raw_input_text,
            input_type=input_type,
            intent=intent,
            delivery_location=delivery_location,
            target_currency=target_currency,
            priority=priority,
            session_token=session_token,
            voice_transcript=voice_transcript,
            source_channel=source_channel,
            metadata=metadata,
        )

        result = intake_service.create_or_update_intake(
            db=db,
            payload=payload,
            user=user,
            idempotency_key=idempotency_key,
            file_bytes=source_bytes,
            file_name=source_file_name,
            file_type=source_file_type,
            file_path=source_file_path,
            audio_bytes=audio_bytes,
            audio_name=audio_file_name,
            audio_type=audio_file_type,
            audio_path=audio_file_path,
        )
        db.commit()

        session = result["session"]
        lifecycle = session.preview_payload or {}
        return IntakeParseResponse(
            intake_session=_to_session_schema(session),
            session_token=session.session_token,
            guest_session_id=session.guest_session_id,
            bom_id=session.bom_id,
            project_id=session.project_id,
            workspace_route=lifecycle.get("workspace_route"),
            analysis_status=session.analysis_status,
            report_visibility_level=lifecycle.get("report_visibility_level", "preview"),
            unlock_status=lifecycle.get("unlock_status", "locked"),
            input_type=result["input_type"],
            intent=result["intent"],
            normalized_text=result["normalized_text"],
            normalized_items=[_to_item_schema(i) for i in session.items],
            parsed_summary=result["summary"],
            confidence_score=result["confidence_score"],
            warnings=session.warnings or [],
            suggestions=session.suggestions or [],
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/normalize", response_model=IntakeParseResponse)
async def normalize_intake(
    raw_input_text: str = Form(None),
    input_type: IntakeInputType = Form(IntakeInputType.auto),
    intent: IntakeIntent = Form(IntakeIntent.auto),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    session_token: str = Form(None),
    voice_transcript: str = Form(None),
    source_channel: str = Form("web"),
    metadata_json: str = Form("{}"),
    source_file: Optional[UploadFile] = File(None),
    audio_file: Optional[UploadFile] = File(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Normalize is intentionally the same parsing pipeline with normalized output.
    return await parse_intake(
        raw_input_text=raw_input_text,
        input_type=input_type,
        intent=intent,
        delivery_location=delivery_location,
        target_currency=target_currency,
        priority=priority,
        session_token=session_token,
        voice_transcript=voice_transcript,
        source_channel=source_channel,
        metadata_json=metadata_json,
        source_file=source_file,
        audio_file=audio_file,
        idempotency_key=idempotency_key,
        user=user,
        db=db,
    )


@router.post("/submit", response_model=IntakeSubmitResponse)
async def submit_intake(
    raw_input_text: str = Form(None),
    input_type: IntakeInputType = Form(IntakeInputType.auto),
    intent: IntakeIntent = Form(IntakeIntent.auto),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    session_token: str = Form(None),
    voice_transcript: str = Form(None),
    source_channel: str = Form("web"),
    async_finalize: bool = Form(True),
    metadata_json: str = Form("{}"),
    source_file: Optional[UploadFile] = File(None),
    audio_file: Optional[UploadFile] = File(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        metadata = {}
        if metadata_json:
            import json
            metadata = json.loads(metadata_json)

        source_file_path = source_file_size = source_file_name = source_file_type = None
        source_bytes = None
        if source_file:
            source_bytes = await source_file.read()
            source_file_path, source_file_size, source_file_name, source_file_type = intake_service._save_upload_file(source_file, "intake", source_bytes)
            source_bytes = source_bytes or b""

        audio_file_path = audio_file_size = audio_file_name = audio_file_type = None
        audio_bytes = None
        if audio_file:
            audio_bytes = await audio_file.read()
            audio_file_path, audio_file_size, audio_file_name, audio_file_type = intake_service._save_upload_file(audio_file, "intake_audio", audio_bytes)
            audio_bytes = audio_bytes or b""

        payload = IntakeSubmitRequest(
            raw_input_text=raw_input_text,
            input_type=input_type,
            intent=intent,
            delivery_location=delivery_location,
            target_currency=target_currency,
            priority=priority,
            session_token=session_token,
            voice_transcript=voice_transcript,
            source_channel=source_channel,
            metadata=metadata,
            async_finalize=async_finalize,
        )

        result = intake_service.finalize_intake_submission(
            db=db,
            payload=payload,
            user=user,
            idempotency_key=idempotency_key,
            file_bytes=source_bytes,
            file_name=source_file_name,
            file_type=source_file_type,
            file_path=source_file_path,
            audio_bytes=audio_bytes,
            audio_name=audio_file_name,
            audio_type=audio_file_type,
            audio_path=audio_file_path,
            async_finalize=async_finalize,
        )
        db.commit()

        session = result["session"]
        return IntakeSubmitResponse(
            intake_session=_to_session_schema(session),
            bom_id=result["bom_id"],
            project_id=result["project_id"],
            analysis_id=result["analysis_id"],
            workspace_route=result["workspace_route"],
            analysis_status=result["analysis_status"],
            report_visibility_level=result["report_visibility_level"],
            unlock_status=result["unlock_status"],
            normalized_items=[_to_item_schema(i) for i in session.items],
            analysis_lifecycle=result["analysis_lifecycle"],
            preview=result["preview"],
            strategy=result["strategy"],
            procurement_plan=result["procurement_plan"],
            parsed_summary=result["parsed_summary"],
            warnings=result["warnings"],
            suggestions=result["suggestions"],
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def _to_item_schema(item):
    return {
        "line_no": item.line_no,
        "raw_text": item.raw_text,
        "item_name": item.item_name,
        "category": item.category,
        "material": item.material,
        "process": item.process,
        "quantity": item.quantity,
        "unit": item.unit,
        "specs": item.specs or {},
        "confidence": item.confidence,
        "warnings": item.warnings or [],
    }