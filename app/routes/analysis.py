"""Analysis routes — FIXED: added auth + ownership check."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.analysis import AnalysisResult
from app.models.user import User
from app.schemas.analysis import AnalysisResponse
from app.utils.dependencies import require_user

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    analysis = db.query(AnalysisResult).filter(AnalysisResult.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis.user_id and analysis.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return analysis


@router.get("/bom/{bom_id}", response_model=AnalysisResponse)
def get_analysis_by_bom(bom_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found for this BOM")
    if analysis.user_id and analysis.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return analysis