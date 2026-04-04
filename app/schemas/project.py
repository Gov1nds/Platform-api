"""Project schemas — control tower + workflow timeline."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: Optional[str] = None
    status: str
    workflow_stage: Optional[str] = None
    visibility_level: Optional[str] = None
    visibility: Optional[str] = None
    total_parts: int = 0
    created_at: Optional[datetime] = None
    cost: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[Any] = None
    file_name: Optional[str] = None
    recommended_location: Optional[str] = None
    currency: Optional[str] = "USD"
    rfq_status: Optional[str] = "none"
    tracking_stage: Optional[str] = "init"
    current_rfq_id: Optional[str] = None
    current_rfq_batch_id: Optional[str] = None
    current_vendor_match_id: Optional[str] = None
    current_vendor_id: Optional[str] = None
    current_quote_id: Optional[str] = None
    current_po_id: Optional[str] = None
    current_shipment_id: Optional[str] = None
    current_invoice_id: Optional[str] = None
    analysis_status: Optional[str] = None
    report_visibility_level: Optional[str] = None
    unlock_status: Optional[str] = None
    workspace_route: Optional[str] = None
    next_action: Optional[str] = None
    spend_summary: Optional[Dict[str, Any]] = None
    analytics_snapshot: Optional[Dict[str, Any]] = None
    savings_realized: Optional[Any] = None
    vendor_on_time_rate: Optional[Any] = None
    quote_to_order_conversion: Optional[Any] = None
    analysis_lifecycle: Optional[Dict[str, Any]] = None
    categories: Optional[Dict[str, Any]] = None
    access: Optional[Dict[str, Any]] = None


class ProjectDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: Optional[str] = None
    status: str
    workflow_stage: Optional[str] = None
    visibility_level: Optional[str] = None
    visibility: Optional[str] = None
    total_parts: int = 0
    file_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    recommended_location: Optional[str] = None
    average_cost: Optional[float] = None
    cost: Optional[float] = None
    cost_range_low: Optional[float] = None
    cost_range_high: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[Any] = None
    currency: Optional[str] = "USD"
    rfq_status: Optional[str] = "none"
    tracking_stage: Optional[str] = "init"
    is_preview: Optional[bool] = None
    guest_bom_id: Optional[str] = None
    session_token: Optional[str] = None
    visible_parts: Optional[List[Dict[str, Any]]] = None
    locked_parts_count: Optional[int] = None
    basic_processes: Optional[Any] = None
    cost_range: Optional[Any] = None
    total_cost: Optional[Any] = None
    risk_level: Optional[str] = None
    unlock_message: Optional[str] = None
    current_analysis_id: Optional[str] = None
    current_strategy_run_id: Optional[str] = None
    current_vendor_match_id: Optional[str] = None
    current_vendor_id: Optional[str] = None
    current_rfq_id: Optional[str] = None
    current_rfq_batch_id: Optional[str] = None
    current_quote_id: Optional[str] = None
    current_po_id: Optional[str] = None
    current_shipment_id: Optional[str] = None
    current_invoice_id: Optional[str] = None
    latest_report_version: Optional[int] = 0
    latest_strategy_version: Optional[int] = 0
    analysis_status: Optional[str] = None
    report_visibility_level: Optional[str] = None
    unlock_status: Optional[str] = None
    workspace_route: Optional[str] = None
    next_action: Optional[str] = None
    spend_summary: Optional[Dict[str, Any]] = None
    analytics_snapshot: Optional[Dict[str, Any]] = None
    savings_realized: Optional[Any] = None
    vendor_on_time_rate: Optional[Any] = None
    quote_to_order_conversion: Optional[Any] = None
    analysis_lifecycle: Optional[Dict[str, Any]] = None
    categories: Optional[Dict[str, Any]] = None
    access: Optional[Dict[str, Any]] = None
    decision_summary: Optional[str] = None
    analyzer_report: Optional[Dict[str, Any]] = None
    strategy: Optional[Dict[str, Any]] = None
    procurement_plan: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

class ProjectActionItem(BaseModel):
    project_id: str
    name: Optional[str] = None
    status: Optional[str] = None
    workflow_stage: Optional[str] = None
    rfq_status: Optional[str] = None
    tracking_stage: Optional[str] = None
    action: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    cost: Optional[float] = None
    savings_percent: Optional[float] = None
    lead_time: Optional[float] = None


class ProjectMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_projects: int = 0
    open_projects: int = 0
    completed_projects: int = 0
    pending_approvals: int = 0
    active_rfqs: int = 0
    delayed_shipments: int = 0
    spend_alerts: int = 0
    total_spend: float = 0.0
    average_savings_percent: float = 0.0
    workflow_counts: Dict[str, int] = {}
    rfq_counts: Dict[str, int] = {}
    pending_approval_items: List[ProjectActionItem] = []
    active_rfq_items: List[ProjectActionItem] = []
    delayed_shipment_items: List[ProjectActionItem] = []
    spend_alert_items: List[ProjectActionItem] = []
    next_actions: List[ProjectActionItem] = []


class ProjectEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    event_type: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    payload: Dict[str, Any] = {}
    actor_user_id: Optional[str] = None
    created_at: Optional[datetime] = None


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None