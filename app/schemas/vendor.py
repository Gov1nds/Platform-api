"""Vendor discovery schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class VendorCapabilitySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    process: str
    material_family: Optional[str] = None
    proficiency: Optional[float] = 0.0
    min_quantity: Optional[float] = None
    max_quantity: Optional[float] = None
    typical_lead_days: Optional[float] = None
    certifications: List[str] = []
    notes: Optional[str] = None
    is_active: Optional[bool] = True


class VendorProfileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    legal_name: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    reliability_score: Optional[float] = 0.0
    avg_lead_time_days: Optional[float] = 0.0
    certifications: List[str] = []
    capabilities: List[str] = []
    memory: Optional[Dict[str, Any]] = None
    capability_entries: List[VendorCapabilitySchema] = []


class VendorMatchItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: str
    project_id: str
    match_run_id: str
    vendor_id: str
    vendor_name: str
    region: Optional[str] = None
    country: Optional[str] = None
    rank: int
    score: float
    reason_codes: List[str] = []
    explanation_json: Dict[str, Any] = {}
    score_breakdown: Dict[str, Any] = {}
    constraint_inputs: Dict[str, Any] = {}
    part_rationales: List[Dict[str, Any]] = []
    shortlist_status: str = "shortlisted"
    response_status: str = "uncontacted"
    feedback_rating: Optional[float] = None
    feedback_notes: Optional[str] = None
    certifications: List[str] = []
    capabilities: List[str] = []
    avg_lead_time_days: Optional[float] = None
    reliability_score: Optional[float] = None
    memory: Optional[Dict[str, Any]] = None
    pricing_summary: Optional[Dict[str, Any]] = None
    scorecard_json: Dict[str, Any] = {}


class VendorMatchRunSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    project_id: str
    user_id: Optional[str] = None
    filters_json: Dict[str, Any] = {}
    constraints_json: Dict[str, Any] = {}
    strategy_snapshot: Dict[str, Any] = {}
    analysis_snapshot: Dict[str, Any] = {}
    weights_json: Dict[str, Any] = {}
    summary_json: Dict[str, Any] = {}
    total_vendors_considered: int = 0
    total_matches: int = 0
    shortlist_size: int = 0
    items: List[VendorMatchItemSchema] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VendorScorecardSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vendor: VendorProfileSchema
    project_id: Optional[str] = None
    project_match: Optional[Dict[str, Any]] = None
    current_memory: Optional[Dict[str, Any]] = None
    pricing_summary: Optional[Dict[str, Any]] = None
    capability_summary: Optional[Dict[str, Any]] = None
    recent_matches: List[Dict[str, Any]] = []
    scorecard: Dict[str, Any] = {}


class VendorFeedbackRequest(BaseModel):
    project_id: Optional[str] = None
    match_run_id: Optional[str] = None
    rating: Optional[float] = None
    notes: Optional[str] = None
    actual_cost: Optional[float] = None
    predicted_cost: Optional[float] = None
    actual_lead_days: Optional[float] = None
    predicted_lead_days: Optional[float] = None
    quality_ok: Optional[bool] = None
    response_status: Optional[str] = None
    response_speed_days: Optional[float] = None