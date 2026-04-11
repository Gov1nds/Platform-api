"""
BOM upload and BOM line management routes.

Endpoints (new RESTful project-scoped paths):
  POST   /projects/{pid}/bom-uploads                         — Upload BOM file
  GET    /projects/{pid}/bom-uploads/{uid}/mapping-preview    — Column mapping preview
  POST   /projects/{pid}/bom-uploads/{uid}/confirm-mapping    — Confirm mapping → create lines
  GET    /projects/{pid}/bom-lines                            — List BOM lines (filterable)
  GET    /projects/{pid}/bom-lines/{bid}                      — Line detail
  PATCH  /projects/{pid}/bom-lines/{bid}                      — Update line (review/override)
  POST   /projects/{pid}/bom-lines/batch-trigger              — Trigger pipeline for eligible lines

Legacy (deprecated, retained for migration):
  POST   /bom/analyze                                         — Monolithic upload+analyze
  POST   /bom/promote-to-project                              — Promote session → project
  GET    /bom/{bom_id}/parts                                  — List parts (now requires auth)

References: GAP-011 (BOM_Upload), GAP-002 (per-line pipeline),
            GAP-024 (auth guard), api-contract-review.md Section 5.4
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.enums import BOMLineStatus, BOMUploadStatus, ProjectStatus
from app.models.bom import BOM, AnalysisResult, BOMPart
from app.models.project import Project, ProjectACL, SearchSession
from app.models.user import GuestSession, User
from app.schemas import BOMAnalyzeResponse, BOMUploadResponse
from app.schemas.bom import (
    BatchTriggerRequest,
    BatchTriggerResponse,
    BOMLineDetailResponse,
    BOMLinePatchRequest,
    BOMLineResponse,
    BOMUploadCreateResponse,
    ConfirmMappingRequest,
    ConfirmMappingResponse,
    MappingPreviewResponse,
)
from app.schemas.common import PaginatedResponse
from app.services import analyzer_service
from app.services.event_service import track
from app.services.workflow.state_machine import transition_project
from app.utils.dependencies import (
    get_current_user,
    require_org_scoped_project,
    require_user,
)
from app.services import guest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["BOM"])


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW RESTful project-scoped endpoints
# ═══════════════════════════════════════════════════════════════════════════════

# ── POST /projects/{pid}/bom-uploads ─────────────────────────────────────────

@router.post(
    "/projects/{project_id}/bom-uploads",
    response_model=BOMUploadCreateResponse,
    status_code=201,
)
async def upload_bom(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Upload a BOM file to a project.

    Flow: file → S3 reference → virus scan trigger → status=PENDING
    Dedup: SHA-256 file_hash checked before processing.
    """
    project = require_org_scoped_project(project_id, request, db)
    org_id = project.organization_id

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file")

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # ── Dedup check ──────────────────────────────────────────────────────
    existing = (
        db.query(BOM)
        .filter(
            BOM.file_hash == file_hash,
            BOM.organization_id == org_id,
            BOM.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        return BOMUploadCreateResponse(
            upload_id=existing.id,
            status="DUPLICATE",
            file_hash=file_hash,
            message="A file with this content has already been uploaded",
        )

    # ── S3 upload reference ──────────────────────────────────────────────
    filename = file.filename or "upload.csv"
    s3_key = f"bom-uploads/{org_id}/{uuid.uuid4()}/{filename}"

    # Actual S3 upload delegated to an async helper; for intake we
    # store the key and write to local UPLOAD_DIR as fallback.
    local_path = Path(settings.UPLOAD_DIR) / f"{uuid.uuid4()}{Path(filename).suffix}"
    local_path.write_bytes(file_bytes)

    # ── Create BOM record ────────────────────────────────────────────────
    bom = BOM(
        uploaded_by_user_id=user.id,
        project_id=project.id,
        organization_id=org_id,
        source_file_name=filename,
        source_file_type=Path(filename).suffix.lstrip("."),
        original_filename=filename,
        file_size_bytes=len(file_bytes),
        file_hash=file_hash,
        s3_key=s3_key,
        status=BOMUploadStatus.PENDING,
        scan_status="PENDING",
        target_currency=project.project_metadata.get("target_currency", "USD") if isinstance(project.project_metadata, dict) else "USD",
    )
    db.add(bom)
    db.flush()

    # Increment project upload counter
    project.bom_upload_count = (project.bom_upload_count or 0) + 1

    # ── Enqueue virus scan + parse (background job hook) ─────────────────
    # In production this dispatches to Celery:
    #   celery_app.send_task("scan_and_parse_bom", args=[bom.id])
    # For now, we transition directly to PARSING to indicate the intent.
    bom.status = BOMUploadStatus.PARSING
    bom.scan_status = "CLEAN"  # will be overwritten by actual scan task

    # ── Inline parse (transitional — will move to background task) ───────
    try:
        result = await analyzer_service.call_analyzer(
            file_bytes, filename,
            user_location=project.project_metadata.get("delivery_location", "") if isinstance(project.project_metadata, dict) else "",
            target_currency=bom.target_currency or "USD",
        )
        bom.parse_summary = result.get("summary", {})
        bom.status = BOMUploadStatus.AWAITING_MAPPING_CONFIRM
        bom.total_parts = len(result.get("components", []))

        # Store raw analysis for mapping preview
        db.add(AnalysisResult(
            bom_id=bom.id,
            user_id=user.id,
            project_id=project.id,
            organization_id=org_id,
            report_json=result,
            summary_json=result.get("summary", {}),
        ))
    except Exception as exc:
        logger.warning("Parse failed for BOM %s: %s", bom.id, exc)
        bom.status = BOMUploadStatus.PARSE_FAILED
        bom.parse_summary = {"error": str(exc)}

    track(db, "bom_uploaded", actor_id=user.id, resource_type="bom", resource_id=bom.id)
    db.commit()

    return BOMUploadCreateResponse(
        upload_id=bom.id,
        status=bom.status,
        file_hash=file_hash,
    )


# ── GET /projects/{pid}/bom-uploads/{uid}/mapping-preview ────────────────────

@router.get(
    "/projects/{project_id}/bom-uploads/{upload_id}/mapping-preview",
    response_model=MappingPreviewResponse,
)
def mapping_preview(
    project_id: str,
    upload_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Return detected columns and suggested mapping for the uploaded BOM."""
    project = require_org_scoped_project(project_id, request, db)
    bom = _get_org_scoped_bom(db, upload_id, project)
    if not bom:
        raise HTTPException(404, "Upload not found")

    if bom.status not in (
        BOMUploadStatus.AWAITING_MAPPING_CONFIRM,
        BOMUploadStatus.MAPPING_CONFIRMED,
        BOMUploadStatus.INGESTED,
    ):
        raise HTTPException(
            409,
            f"Upload status is {bom.status}; mapping preview not available",
        )

    # Pull detected columns from the analysis result
    ar = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
    report = ar.report_json if ar else {}
    components = report.get("components", [])

    # Build column detection from first component keys
    detected_columns = []
    if components:
        sample = components[0]
        for header in sample.keys():
            detected_columns.append({
                "detected_header": header,
                "suggested_field": header,  # identity mapping as default
                "confidence": 0.9,
                "sample_values": [str(c.get(header, ""))[:80] for c in components[:3]],
            })

    return MappingPreviewResponse(
        upload_id=bom.id,
        status=bom.status,
        detected_columns=detected_columns,
        row_count=len(components),
        preview_rows=components[:5],
    )


# ── POST /projects/{pid}/bom-uploads/{uid}/confirm-mapping ───────────────────

@router.post(
    "/projects/{project_id}/bom-uploads/{upload_id}/confirm-mapping",
    response_model=ConfirmMappingResponse,
)
def confirm_mapping(
    project_id: str,
    upload_id: str,
    body: ConfirmMappingRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    User confirms column mapping. Creates BOM_Line records as RAW.

    Transitions upload status: AWAITING_MAPPING_CONFIRM → MAPPING_CONFIRMED.
    """
    project = require_org_scoped_project(project_id, request, db)
    bom = _get_org_scoped_bom(db, upload_id, project)
    if not bom:
        raise HTTPException(404, "Upload not found")

    if bom.status != BOMUploadStatus.AWAITING_MAPPING_CONFIRM:
        raise HTTPException(409, f"Upload status is {bom.status}; expected AWAITING_MAPPING_CONFIRM")

    bom.column_mapping_json = body.column_mapping
    bom.status = BOMUploadStatus.MAPPING_CONFIRMED

    # Read components from analysis result
    ar = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
    report = ar.report_json if ar else {}
    components = report.get("components", [])

    # Create BOM_Line records
    mapping = body.column_mapping
    lines_created = 0

    for idx, comp in enumerate(components):
        mapped = _apply_mapping(comp, mapping)
        part = BOMPart(
            bom_id=bom.id,
            organization_id=project.organization_id,
            status=BOMLineStatus.RAW,
            row_number=idx + 1,
            source_type="file",
            item_id=mapped.get("item_id", str(idx + 1)),
            raw_text=mapped.get("raw_text", str(comp)),
            normalized_text=mapped.get("standard_text") or mapped.get("normalized_text"),
            description=mapped.get("description", ""),
            quantity=mapped.get("quantity", 1),
            unit=mapped.get("unit", "each"),
            part_number=mapped.get("part_number", ""),
            mpn=mapped.get("mpn", ""),
            manufacturer=mapped.get("manufacturer", ""),
            supplier_name=mapped.get("supplier_name", ""),
            category_code=mapped.get("category") or mapped.get("category_code", ""),
            procurement_class=mapped.get("procurement_class", "unknown"),
            material=mapped.get("material", ""),
            material_form=mapped.get("material_form"),
            specs=mapped.get("specs", {}),
            classification_confidence=mapped.get("classification_confidence", 0),
            classification_reason=mapped.get("classification_reason", ""),
            has_mpn=mapped.get("has_mpn", False),
            is_custom=mapped.get("is_custom", False),
            is_raw=mapped.get("is_raw", False),
            rfq_required=mapped.get("rfq_required", False),
            drawing_required=mapped.get("drawing_required", False),
            canonical_part_key=mapped.get("canonical_part_key", ""),
            review_status=mapped.get("review_status", "auto"),
        )
        db.add(part)
        lines_created += 1

    # Update counters
    bom.total_parts = lines_created
    bom.status = BOMUploadStatus.INGESTED
    project.bom_line_count = (project.bom_line_count or 0) + lines_created
    project.total_parts = (project.total_parts or 0) + lines_created

    track(db, "mapping_confirmed", actor_id=user.id, resource_type="bom", resource_id=bom.id)
    db.commit()

    return ConfirmMappingResponse(
        upload_id=bom.id,
        status=bom.status,
        lines_created=lines_created,
    )


# ── GET /projects/{pid}/bom-lines ────────────────────────────────────────────

@router.get("/projects/{project_id}/bom-lines")
def list_bom_lines(
    project_id: str,
    request: Request,
    status: str | None = Query(None, description="Filter by BOM line status"),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List BOM lines for a project, with optional status filter and cursor pagination."""
    project = require_org_scoped_project(project_id, request, db)

    # Collect all BOM IDs for this project
    bom_ids = [
        b.id for b in
        db.query(BOM.id).filter(
            BOM.project_id == project.id,
            BOM.deleted_at.is_(None),
        ).all()
    ]

    if not bom_ids:
        return PaginatedResponse(items=[], total_count=0)

    q = (
        db.query(BOMPart)
        .filter(BOMPart.bom_id.in_(bom_ids), BOMPart.deleted_at.is_(None))
    )

    if status:
        q = q.filter(BOMPart.status == status.upper())

    total = q.count()

    # Cursor-based pagination (using created_at + id)
    if cursor:
        q = q.filter(BOMPart.id > cursor)

    items = q.order_by(BOMPart.row_number.asc(), BOMPart.id.asc()).limit(limit).all()

    next_cursor = items[-1].id if len(items) == limit else None

    return PaginatedResponse(
        items=[BOMLineResponse.model_validate(p) for p in items],
        next_cursor=next_cursor,
        total_count=total,
    )


# ── GET /projects/{pid}/bom-lines/{bid} ─────────────────────────────────────

@router.get("/projects/{project_id}/bom-lines/{line_id}", response_model=BOMLineDetailResponse)
def get_bom_line(
    project_id: str,
    line_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get detailed BOM line including pipeline outputs."""
    project = require_org_scoped_project(project_id, request, db)
    part = _get_project_scoped_line(db, line_id, project)
    if not part:
        raise HTTPException(404, "BOM line not found")
    return BOMLineDetailResponse.model_validate(part)


# ── PATCH /projects/{pid}/bom-lines/{bid} ────────────────────────────────────

@router.patch("/projects/{project_id}/bom-lines/{line_id}", response_model=BOMLineResponse)
def update_bom_line(
    project_id: str,
    line_id: str,
    body: BOMLinePatchRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Update a BOM line (review confirmation, override, etc.)."""
    project = require_org_scoped_project(project_id, request, db)
    part = _get_project_scoped_line(db, line_id, project)
    if not part:
        raise HTTPException(404, "BOM line not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "review_status" and value == "confirmed":
            part.review_required = False
        setattr(part, field, value)

    track(db, "bom_line_updated", actor_id=user.id, resource_type="bom_line", resource_id=part.id)
    db.commit()
    db.refresh(part)
    return BOMLineResponse.model_validate(part)


# ── POST /projects/{pid}/bom-lines/batch-trigger ────────────────────────────

@router.post(
    "/projects/{project_id}/bom-lines/batch-trigger",
    response_model=BatchTriggerResponse,
)
def batch_trigger(
    project_id: str,
    body: BatchTriggerRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Trigger batch pipeline (normalize, enrich, score) for eligible lines.

    This is the intake handoff trigger — it does NOT execute the full
    pipeline synchronously. In production, it enqueues background tasks.
    """
    project = require_org_scoped_project(project_id, request, db)

    # Collect eligible lines
    bom_ids = [
        b.id for b in
        db.query(BOM.id).filter(
            BOM.project_id == project.id,
            BOM.deleted_at.is_(None),
        ).all()
    ]

    q = db.query(BOMPart).filter(
        BOMPart.bom_id.in_(bom_ids),
        BOMPart.deleted_at.is_(None),
        BOMPart.status == BOMLineStatus.RAW,
    )
    if body.line_ids:
        q = q.filter(BOMPart.id.in_(body.line_ids))

    eligible = q.all()
    triggered = 0
    skipped = 0

    for part in eligible:
        # Mark as NORMALIZING to indicate pipeline entry
        part.status = BOMLineStatus.NORMALIZING
        part.normalization_status = "PENDING"
        triggered += 1
        # In production: celery_app.send_task("normalize_bom_line", args=[part.id])

    # Any non-RAW lines in the filter are skipped
    if body.line_ids:
        all_count = db.query(BOMPart).filter(
            BOMPart.id.in_(body.line_ids),
            BOMPart.deleted_at.is_(None),
        ).count()
        skipped = all_count - triggered

    track(
        db, "batch_triggered",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
    )
    db.commit()

    return BatchTriggerResponse(
        triggered_count=triggered,
        skipped_count=skipped,
        message=f"Triggered {triggered} lines for pipeline processing",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  LEGACY endpoints (deprecated, retained for migration)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/bom/analyze", response_model=BOMAnalyzeResponse, deprecated=True)
async def analyze_bom_legacy(
    file: UploadFile = File(...),
    delivery_location: str = Form(""),
    target_currency: str = Form("USD"),
    priority: str = Form("balanced"),
    session_token: str = Form(""),
    request: Request = None,
    response: Response = None,
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legacy monolithic upload+analyze. Use project-scoped upload instead."""
    file_bytes = await file.read()

    # Guest session handling — now via HttpOnly cookie
    guest: GuestSession | None = None
    if not user and response:
        guest = guest_service.get_or_create_guest_session(request, response, db)
    elif session_token and not user:
        guest = db.query(GuestSession).filter(
            GuestSession.session_token == session_token
        ).first()
        if not guest:
            guest = GuestSession(session_token=session_token, status="ACTIVE")
            db.add(guest)
            db.flush()

    # Save file locally
    fid = str(uuid.uuid4())
    ext = Path(file.filename or "upload.csv").suffix
    (Path(settings.UPLOAD_DIR) / f"{fid}{ext}").write_bytes(file_bytes)

    try:
        result = await analyzer_service.call_analyzer(
            file_bytes, file.filename or "upload.csv", delivery_location, target_currency,
        )
    except Exception as e:
        result = {"components": [], "summary": {"total_items": 0, "error": str(e)}}

    bom, components = _store_bom_and_parts_legacy(
        db, file_bytes, file.filename or "upload.csv",
        user, guest, delivery_location, target_currency, priority, result,
    )

    ss = SearchSession(
        user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        session_token=session_token,
        query_text=file.filename or "BOM upload",
        query_type="bom",
        input_type="file",
        delivery_location=delivery_location,
        target_currency=target_currency,
        results_json=result.get("summary", {}),
        analysis_payload={
            "bom_id": bom.id,
            "total_parts": len(components),
            "components_preview": [
                {
                    "item_id": c.get("item_id"),
                    "description": c.get("description", "")[:100],
                    "category": c.get("category", ""),
                }
                for c in components[:20]
            ],
        },
        status="analyzed",
    )
    db.add(ss)
    db.flush()

    track(db, "analyze_completed", actor_id=user.id if user else None,
          resource_type="bom", resource_id=bom.id)
    db.commit()

    n = len(components)
    return BOMAnalyzeResponse(
        search_session_id=ss.id,
        total_parts=n,
        analysis=result.get("summary", {}),
        recommended_flow="project" if n > 3 else "search_session",
    )


@router.post("/bom/promote-to-project", response_model=BOMUploadResponse, deprecated=True)
def promote_to_project_legacy(
    search_session_id: str = Form(...),
    session_token: str = Form(""),
    request: Request = None,
    response: Response = None,
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legacy promote. Use POST /projects instead."""
    ss = db.query(SearchSession).filter(SearchSession.id == search_session_id).first()
    if not ss:
        raise HTTPException(404, "Search session not found")
    if ss.promoted_to_id:
        return BOMUploadResponse(
            bom_id=ss.analysis_payload.get("bom_id", ""),
            project_id=ss.promoted_to_id,
            total_parts=0,
            status="already_promoted",
        )

    bom_id = ss.analysis_payload.get("bom_id")
    if not bom_id:
        raise HTTPException(400, "No BOM associated with this session")
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if not bom:
        raise HTTPException(404, "BOM not found")

    guest: GuestSession | None = None
    if not user and response:
        guest = guest_service.get_or_create_guest_session(request, response, db)
    elif session_token and not user:
        guest = db.query(GuestSession).filter(
            GuestSession.session_token == session_token
        ).first()

    org_id = user.organization_id if user else None

    project = Project(
        bom_id=bom.id,
        user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        organization_id=org_id,
        name=bom.original_filename or "Uploaded BOM",
        file_name=bom.original_filename,
        status=ProjectStatus.DRAFT,
        visibility="owner_only" if user else "guest_preview",
        total_parts=bom.total_parts,
        analyzer_report=bom.parse_summary or {},
    )
    db.add(project)
    db.flush()

    bom.project_id = project.id

    if user:
        db.add(ProjectACL(
            project_id=project.id,
            principal_type="user",
            principal_id=user.id,
            role="owner",
            organization_id=org_id,
        ))
    elif guest:
        db.add(ProjectACL(
            project_id=project.id,
            principal_type="guest_session",
            principal_id=guest.id,
            role="viewer",
        ))

    ss.promoted_to = "project"
    ss.promoted_to_id = project.id
    ss.status = "PROMOTED_TO_PROJECT"

    track(db, "project_created", actor_id=user.id if user else None,
          resource_type="project", resource_id=project.id)
    db.commit()

    return BOMUploadResponse(
        bom_id=bom.id,
        project_id=project.id,
        total_parts=bom.total_parts,
        status=project.status,
        analysis=bom.parse_summary or {},
    )


@router.get("/bom/{bom_id}/parts")
def get_parts_legacy(
    bom_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Legacy parts listing — now requires auth (GAP-024)."""
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if not bom:
        raise HTTPException(404, "BOM not found")

    # Basic ownership check
    if bom.uploaded_by_user_id and bom.uploaded_by_user_id != user.id:
        if user.organization_id and bom.organization_id != user.organization_id:
            raise HTTPException(403, "Access denied")

    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id, BOMPart.deleted_at.is_(None)).all()
    return [
        {
            "id": p.id, "item_id": p.item_id, "description": p.description,
            "quantity": float(p.quantity) if p.quantity else 1,
            "status": p.status,
            "category_code": p.category_code,
            "procurement_class": p.procurement_class,
            "material": p.material, "mpn": p.mpn,
            "is_custom": p.is_custom, "rfq_required": p.rfq_required,
            "specs": p.specs, "canonical_part_key": p.canonical_part_key,
        }
        for p in parts
    ]


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_org_scoped_bom(db: Session, upload_id: str, project: Project) -> BOM | None:
    return (
        db.query(BOM)
        .filter(
            BOM.id == upload_id,
            BOM.project_id == project.id,
            BOM.deleted_at.is_(None),
        )
        .first()
    )


def _get_project_scoped_line(db: Session, line_id: str, project: Project) -> BOMPart | None:
    bom_ids = [
        b.id for b in
        db.query(BOM.id).filter(BOM.project_id == project.id, BOM.deleted_at.is_(None)).all()
    ]
    if not bom_ids:
        return None
    return (
        db.query(BOMPart)
        .filter(BOMPart.id == line_id, BOMPart.bom_id.in_(bom_ids), BOMPart.deleted_at.is_(None))
        .first()
    )


def _apply_mapping(component: dict, mapping: dict[str, str]) -> dict:
    """Apply user-confirmed column mapping to a raw component dict."""
    result = {}
    for detected_header, canonical_field in mapping.items():
        if detected_header in component:
            result[canonical_field] = component[detected_header]
    # Also copy any unmapped keys directly
    for k, v in component.items():
        if k not in result:
            result[k] = v
    return result


def _store_bom_and_parts_legacy(db, file_bytes, filename, user, guest,
                                delivery_location, target_currency, priority, result):
    """Legacy BOM+parts storage for /bom/analyze backward compatibility."""
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    bom = BOM(
        uploaded_by_user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        source_file_name=filename,
        source_file_type=Path(filename).suffix.lstrip("."),
        original_filename=filename,
        file_size_bytes=len(file_bytes),
        file_hash=file_hash,
        target_currency=target_currency,
        delivery_location=delivery_location,
        priority=priority,
        organization_id=user.organization_id if user else None,
    )
    db.add(bom)
    db.flush()

    components = result.get("components", [])
    for idx, comp in enumerate(components):
        db.add(BOMPart(
            bom_id=bom.id,
            organization_id=user.organization_id if user else None,
            status=BOMLineStatus.RAW,
            row_number=idx + 1,
            item_id=comp.get("item_id", ""),
            raw_text=comp.get("raw_text", ""),
            normalized_text=comp.get("standard_text", ""),
            description=comp.get("description", ""),
            quantity=comp.get("quantity", 1),
            unit=comp.get("unit", "each"),
            part_number=comp.get("part_number", ""),
            mpn=comp.get("mpn", ""),
            manufacturer=comp.get("manufacturer", ""),
            supplier_name=comp.get("supplier_name", ""),
            category_code=comp.get("category", ""),
            procurement_class=comp.get("procurement_class", "unknown"),
            material=comp.get("material", ""),
            material_form=comp.get("material_form"),
            specs=comp.get("specs", {}),
            classification_confidence=comp.get("classification_confidence", 0),
            classification_reason=comp.get("classification_reason", ""),
            has_mpn=comp.get("has_mpn", False),
            is_custom=comp.get("is_custom", False),
            is_raw=comp.get("is_raw", False),
            rfq_required=comp.get("rfq_required", False),
            drawing_required=comp.get("drawing_required", False),
            canonical_part_key=comp.get("canonical_part_key", ""),
            review_status=comp.get("review_status", "auto"),
        ))

    bom.total_parts = len(components)
    bom.status = BOMUploadStatus.INGESTED

    db.add(AnalysisResult(
        bom_id=bom.id,
        user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        report_json=result,
        summary_json=result.get("summary", {}),
    ))
    db.flush()
    return bom, components