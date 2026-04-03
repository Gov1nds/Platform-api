"""Analysis schemas."""
from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class AnalysisResponse(BaseModel):
    id: str
    bom_id: str
    recommended_location: Optional[str] = None
    average_cost: Optional[float] = None
    cost_range_low: Optional[float] = None
    cost_range_high: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None
    analysis_status: Optional[str] = None
    report_visibility_level: Optional[str] = None
    unlock_status: Optional[str] = None
    workspace_route: Optional[str] = None
    decision_summary: Optional[str] = None
    strategy_output: Optional[Dict[str, Any]] = None
    enriched_output: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
