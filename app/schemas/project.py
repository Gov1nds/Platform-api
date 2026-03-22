"""Project schemas."""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class ProjectSummary(BaseModel):
    """List view — one card per project."""
    project_id: str
    name: Optional[str] = None
    status: str
    total_parts: int = 0
    created_at: Optional[datetime] = None
    cost: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None
    file_name: Optional[str] = None

    class Config:
        from_attributes = True


class ProjectDetail(BaseModel):
    """Full project view with analysis + strategy."""
    project_id: str
    name: Optional[str] = None
    status: str
    total_parts: int = 0
    file_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Analysis
    recommended_location: Optional[str] = None
    average_cost: Optional[float] = None
    cost_range_low: Optional[float] = None
    cost_range_high: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None
    decision_summary: Optional[str] = None
    # Full data
    analyzer_report: Optional[Dict[str, Any]] = None
    strategy: Optional[Dict[str, Any]] = None
    procurement_plan: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class StatusUpdate(BaseModel):
    """Request body for status change."""
    status: str
    notes: Optional[str] = None