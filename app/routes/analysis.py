"""Analysis routes — updated for bom.analysis_results."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.analysis import AnalysisResult
from app.models.user import User, GuestSession
from app.schemas.analysis import AnalysisResponse
from app.services import project_service
from app.utils.dependencies import require_user, can_access_project

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _guest_session_matches(session_token: str | None, guest_session_id: str | None, db: Session) -> bool:
    if not session_token or not guest_session_id:
        return False
    guest = db.query(GuestSession).filter(GuestSession.session_token == session_token).first()
    return bool(guest and str(guest.id) == str(guest_session_id))


@router.get("/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(
    analysis_id: str,
    session_token: str | None = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    analysis = db.query(AnalysisResult).filter(AnalysisResult.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if analysis.user_id and str(analysis.user_id) == str(user.id):
        return analysis

    project = project_service.get_project_by_bom_id(db, analysis.bom_id)
    if project and can_access_project(user, project):
        return analysis

    if _guest_session_matches(session_token, analysis.guest_session_id, db):
        return analysis

    raise HTTPException(status_code=403, detail="Not authorized")


@router.get("/bom/{bom_id}", response_model=AnalysisResponse)
def get_analysis_by_bom(
    bom_id: str,
    session_token: str | None = Query(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found for this BOM")

    if analysis.user_id and str(analysis.user_id) == str(user.id):
        return analysis

    project = project_service.get_project_by_bom_id(db, bom_id)
    if project and can_access_project(user, project):
        return analysis

    if _guest_session_matches(session_token, analysis.guest_session_id, db):
        return analysis

    raise HTTPException(status_code=403, detail="Not authorized")
