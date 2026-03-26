"""Project schemas — FIXED: added currency field."""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: Optional[str] = None
    status: str
    total_parts: int = 0
    created_at: Optional[datetime] = None
    cost: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None
    file_name: Optional[str] = None
    recommended_location: Optional[str] = None
    currency: Optional[str] = "USD"  # NEW


class ProjectDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: Optional[str] = None
    status: str
    total_parts: int = 0
    file_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    recommended_location: Optional[str] = None
    average_cost: Optional[float] = None
    cost_range_low: Optional[float] = None
    cost_range_high: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None
    currency: Optional[str] = "USD"  # NEW
    decision_summary: Optional[str] = None
    analyzer_report: Optional[Dict[str, Any]] = None
    strategy: Optional[Dict[str, Any]] = None
    procurement_plan: Optional[Dict[str, Any]] = None


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None