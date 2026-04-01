"""Analytics response schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsFilterRequest(BaseModel):
    project_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class SpendAnalyticsResponse(BaseModel):
    totals: Dict[str, Any] = Field(default_factory=dict)
    by_vendor: List[Dict[str, Any]] = Field(default_factory=list)
    by_category: List[Dict[str, Any]] = Field(default_factory=list)
    by_region: List[Dict[str, Any]] = Field(default_factory=list)
    monthly: List[Dict[str, Any]] = Field(default_factory=list)
    quote_to_order_conversion: Optional[float] = None
    vendor_on_time_rate: Optional[float] = None
    filters: Dict[str, Any] = Field(default_factory=dict)


class VendorAnalyticsResponse(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict)
    vendors: List[Dict[str, Any]] = Field(default_factory=list)


class CategoryAnalyticsResponse(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict)
    categories: List[Dict[str, Any]] = Field(default_factory=list)


class TrendAnalyticsResponse(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict)
    monthly: List[Dict[str, Any]] = Field(default_factory=list)
    lead_time_trend: List[Dict[str, Any]] = Field(default_factory=list)
    quote_to_order_conversion: List[Dict[str, Any]] = Field(default_factory=list)
    vendor_on_time_rate: List[Dict[str, Any]] = Field(default_factory=list)


class SavingsAnalyticsResponse(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict)
    savings: List[Dict[str, Any]] = Field(default_factory=list)
    totals: Dict[str, Any] = Field(default_factory=dict)


class ReportScheduleRequest(BaseModel):
    report_name: str
    report_type: str
    frequency: str = "weekly"
    recipients_json: List[str] = Field(default_factory=list)
    filters_json: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    next_run_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReportScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    report_name: str
    report_type: str
    frequency: str
    recipients_json: List[str] = Field(default_factory=list)
    filters_json: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None