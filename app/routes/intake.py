from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.models.user import User, GuestSession
from app.models.project import SearchSession, SourcingCase, Project, ProjectACL
from app.models.bom import BOM
from app.schemas import SearchSessionResponse, SourcingCaseResponse
from app.utils.dependencies import get_current_user
from app.services.event_service import track

router = APIRouter(prefix="/intake", tags=["Intake"])

def _gs(db, token):
    if not token: return None
    gs = db.query(GuestSession).filter(GuestSession.session_token == token).first()
    if not gs: gs = GuestSession(session_token=token); db.add(gs); db.flush()
    return gs

@router.post("/search", response_model=SearchSessionResponse)
def create_search(query_text:str=Form(""), query_type:str=Form("component"), session_token:str=Form(""),
                  delivery_location:str=Form(""), target_currency:str=Form("USD"),
                  user:User|None=Depends(get_current_user), db:Session=Depends(get_db)):
    guest = _gs(db, session_token) if session_token and not user else None
    ss = SearchSession(user_id=user.id if user else None, guest_session_id=guest.id if guest else None,
        session_token=session_token, query_text=query_text, query_type=query_type, input_type="text",
        delivery_location=delivery_location, target_currency=target_currency, status="active")
    db.add(ss); db.commit(); db.refresh(ss)
    return SearchSessionResponse.model_validate(ss)

@router.get("/search/{session_id}", response_model=SearchSessionResponse)
def get_search(session_id:str, db:Session=Depends(get_db)):
    ss = db.query(SearchSession).filter(SearchSession.id == session_id).first()
    if not ss: raise HTTPException(404)
    return SearchSessionResponse.model_validate(ss)

@router.post("/search/{session_id}/save", response_model=SourcingCaseResponse)
def save_as_sourcing_case(session_id:str, name:str=Form("Saved search"), notes:str=Form(""),
                          session_token:str=Form(""), user:User|None=Depends(get_current_user),
                          db:Session=Depends(get_db)):
    ss = db.query(SearchSession).filter(SearchSession.id == session_id).first()
    if not ss: raise HTTPException(404)
    if ss.promoted_to == "sourcing_case" and ss.promoted_to_id:
        sc = db.query(SourcingCase).filter(SourcingCase.id == ss.promoted_to_id).first()
        if sc: return SourcingCaseResponse.model_validate(sc)
    guest = _gs(db, session_token) if session_token and not user else None
    sc = SourcingCase(user_id=user.id if user else None, guest_session_id=guest.id if guest else None,
        session_token=session_token, search_session_id=ss.id, name=name,
        query_text=ss.query_text, analysis_payload=ss.analysis_payload, notes=notes, status="active")
    db.add(sc); db.flush()
    ss.promoted_to = "sourcing_case"; ss.promoted_to_id = sc.id; ss.status = "saved"
    db.commit()
    return SourcingCaseResponse.model_validate(sc)

@router.post("/sourcing-case/{case_id}/promote")
def promote_case_to_project(case_id:str, session_token:str=Form(""),
                            user:User|None=Depends(get_current_user), db:Session=Depends(get_db)):
    sc = db.query(SourcingCase).filter(SourcingCase.id == case_id).first()
    if not sc: raise HTTPException(404)
    if sc.promoted_to_project_id:
        return {"project_id":sc.promoted_to_project_id,"status":"already_promoted"}
    bom_id = sc.analysis_payload.get("bom_id")
    if not bom_id:
        bom = BOM(uploaded_by_user_id=user.id if user else None, source_file_name="sourcing_case", status="uploaded")
        db.add(bom); db.flush(); bom_id = bom.id
    guest = _gs(db, session_token) if session_token and not user else None
    project = Project(bom_id=bom_id, user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None, sourcing_case_id=sc.id,
        name=sc.name, status="draft", visibility="owner_only" if user else "guest_preview")
    db.add(project); db.flush()
    if user: db.add(ProjectACL(project_id=project.id, principal_type="user", principal_id=user.id, role="owner"))
    sc.promoted_to_project_id = project.id; sc.status = "promoted"
    track(db,"project_created",actor_id=user.id if user else None,resource_type="project",resource_id=project.id)
    db.commit()
    return {"project_id":project.id,"status":"promoted"}

@router.get("/sourcing-cases")
def list_sourcing_cases(session_token:str=Query(""), user:User|None=Depends(get_current_user), db:Session=Depends(get_db)):
    q = db.query(SourcingCase)
    if user: q = q.filter(SourcingCase.user_id == user.id)
    elif session_token: q = q.filter(SourcingCase.session_token == session_token)
    else: return []
    return [SourcingCaseResponse.model_validate(sc) for sc in q.order_by(SourcingCase.created_at.desc()).limit(50).all()]

@router.get("/sessions")
def list_sessions(session_token:str=Query(""), user:User|None=Depends(get_current_user), db:Session=Depends(get_db)):
    q = db.query(SearchSession)
    if user: q = q.filter(SearchSession.user_id == user.id)
    elif session_token: q = q.filter(SearchSession.session_token == session_token)
    else: return []
    return [SearchSessionResponse.model_validate(s) for s in q.order_by(SearchSession.created_at.desc()).limit(50).all()]
