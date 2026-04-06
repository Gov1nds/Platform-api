import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User, GuestSession
from app.models.bom import BOM, BOMPart, AnalysisResult
from app.models.project import Project, ProjectACL, SearchSession
from app.schemas import BOMAnalyzeResponse, BOMUploadResponse
from app.utils.dependencies import get_current_user
from app.services import analyzer_service
from app.services.event_service import track
from app.services.workflow.state_machine import transition_project

router = APIRouter(prefix="/bom", tags=["BOM"])

def _ensure_guest(db, token):
    if not token: return None
    gs = db.query(GuestSession).filter(GuestSession.session_token == token).first()
    if not gs: gs = GuestSession(session_token=token); db.add(gs); db.flush()
    return gs

def _store_bom_and_parts(db, file_bytes, filename, user, guest, delivery_location, target_currency, priority, result):
    bom = BOM(
        uploaded_by_user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        source_file_name=filename, source_file_type=Path(filename).suffix.lstrip("."),
        original_filename=filename, file_size_bytes=len(file_bytes),
        target_currency=target_currency, delivery_location=delivery_location, priority=priority,
    )
    db.add(bom); db.flush()
    components = result.get("components",[])
    for comp in components:
        db.add(BOMPart(
            bom_id=bom.id, item_id=comp.get("item_id",""), raw_text=comp.get("raw_text",""),
            normalized_text=comp.get("standard_text",""), description=comp.get("description",""),
            quantity=comp.get("quantity",1), unit=comp.get("unit","each"),
            part_number=comp.get("part_number",""), mpn=comp.get("mpn",""),
            manufacturer=comp.get("manufacturer",""), supplier_name=comp.get("supplier_name",""),
            category_code=comp.get("category",""), procurement_class=comp.get("procurement_class","unknown"),
            material=comp.get("material",""), material_form=comp.get("material_form"),
            specs=comp.get("specs",{}), classification_confidence=comp.get("classification_confidence",0),
            classification_reason=comp.get("classification_reason",""),
            has_mpn=comp.get("has_mpn",False), is_custom=comp.get("is_custom",False),
            is_raw=comp.get("is_raw",False), rfq_required=comp.get("rfq_required",False),
            drawing_required=comp.get("drawing_required",False),
            canonical_part_key=comp.get("canonical_part_key",""),
            review_status=comp.get("review_status","auto"),
        ))
    bom.total_parts = len(components); bom.status = "analyzed"
    bom.raw_payload = result.get("summary",{})
    db.add(AnalysisResult(bom_id=bom.id, user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None, report_json=result, summary_json=result.get("summary",{})))
    db.flush()
    return bom, components

@router.post("/analyze", response_model=BOMAnalyzeResponse)
async def analyze_bom(
    file: UploadFile = File(...),
    delivery_location: str = Form(""), target_currency: str = Form("USD"),
    priority: str = Form("balanced"), session_token: str = Form(""),
    user: User|None = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Analyze a BOM file → returns search session. Does NOT create a project."""
    file_bytes = await file.read()
    guest = _ensure_guest(db, session_token) if session_token and not user else None

    # Save file
    fid = str(uuid.uuid4()); ext = Path(file.filename or "upload.csv").suffix
    (Path(settings.UPLOAD_DIR)/f"{fid}{ext}").write_bytes(file_bytes)

    try:
        result = await analyzer_service.call_analyzer(file_bytes, file.filename or "upload.csv", delivery_location, target_currency)
    except Exception as e:
        result = {"components":[],"summary":{"total_items":0,"error":str(e)}}

    bom, components = _store_bom_and_parts(db, file_bytes, file.filename or "upload.csv", user, guest, delivery_location, target_currency, priority, result)

    # Create search session (not a project)
    ss = SearchSession(
        user_id=user.id if user else None, guest_session_id=guest.id if guest else None,
        session_token=session_token, query_text=file.filename or "BOM upload",
        query_type="bom", input_type="file", delivery_location=delivery_location,
        target_currency=target_currency, results_json=result.get("summary",{}),
        analysis_payload={"bom_id":bom.id, "total_parts":len(components), "components_preview":[
            {"item_id":c.get("item_id"),"description":c.get("description","")[:100],"category":c.get("category",""),
             "cost_estimate":c.get("cost_estimate",{}),"risk_assessment":c.get("risk_assessment",{})}
            for c in components[:20]
        ]},
        status="analyzed",
    )
    db.add(ss); db.flush()
    track(db,"analyze_completed",actor_id=user.id if user else None,resource_type="bom",resource_id=bom.id)
    db.commit()

    n = len(components)
    return BOMAnalyzeResponse(
        search_session_id=ss.id, total_parts=n, analysis=result.get("summary",{}),
        recommended_flow="project" if n > 3 else "search_session",
    )

@router.post("/promote-to-project", response_model=BOMUploadResponse)
def promote_to_project(
    search_session_id: str = Form(...), session_token: str = Form(""),
    user: User|None = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Promote a search session to a full project."""
    ss = db.query(SearchSession).filter(SearchSession.id == search_session_id).first()
    if not ss: raise HTTPException(404, "Search session not found")
    if ss.promoted_to_id: return BOMUploadResponse(bom_id=ss.analysis_payload.get("bom_id",""),project_id=ss.promoted_to_id,total_parts=0,status="already_promoted")

    bom_id = ss.analysis_payload.get("bom_id")
    if not bom_id: raise HTTPException(400, "No BOM associated with this session")
    bom = db.query(BOM).filter(BOM.id == bom_id).first()
    if not bom: raise HTTPException(404, "BOM not found")

    guest = _ensure_guest(db, session_token) if session_token and not user else None
    project = Project(
        bom_id=bom.id, user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
        name=bom.original_filename or "Uploaded BOM", file_name=bom.original_filename,
        status="analyzed", visibility="owner_only" if user else "guest_preview",
        total_parts=bom.total_parts,
        analyzer_report=bom.raw_payload,
    )
    db.add(project); db.flush()
    bom.project_id = project.id

    # ACL
    if user: db.add(ProjectACL(project_id=project.id, principal_type="user", principal_id=user.id, role="owner"))
    elif guest: db.add(ProjectACL(project_id=project.id, principal_type="guest_session", principal_id=guest.id, role="viewer"))

    ss.promoted_to = "project"; ss.promoted_to_id = project.id; ss.status = "promoted"
    track(db,"project_created",actor_id=user.id if user else None,resource_type="project",resource_id=project.id)
    db.commit()

    return BOMUploadResponse(bom_id=bom.id, project_id=project.id, total_parts=bom.total_parts, status="analyzed", analysis=bom.raw_payload or {})

@router.get("/{bom_id}/parts")
def get_parts(bom_id: str, db: Session = Depends(get_db)):
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).all()
    return [{"id":p.id,"item_id":p.item_id,"description":p.description,"quantity":float(p.quantity),"category_code":p.category_code,"procurement_class":p.procurement_class,"material":p.material,"mpn":p.mpn,"is_custom":p.is_custom,"rfq_required":p.rfq_required,"specs":p.specs,"canonical_part_key":p.canonical_part_key} for p in parts]
