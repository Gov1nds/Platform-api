"""Analysis routes — retrieve stored analysis results."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.analysis import AnalysisResult
from app.schemas.analysis import AnalysisResponse

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """Get a stored analysis result by ID."""
    analysis = db.query(AnalysisResult).filter(AnalysisResult.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@router.get("/bom/{bom_id}", response_model=AnalysisResponse)
def get_analysis_by_bom(bom_id: str, db: Session = Depends(get_db)):
    """Get analysis result for a specific BOM."""
    analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found for this BOM")
    return analysis
