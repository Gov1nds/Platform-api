"""RFQ schemas — normalized quotes, comparison matrix, selection and rejection."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class RFQCreateRequest(BaseModel):
    bom_id: str
    notes: Optional[str] = None


class RFQQuoteItem(BaseModel):
    part_name: str
    price: float
    lead_time: Optional[float] = None
    availability_status: Optional[str] = None
    compliance_status: Optional[str] = None
    moq: Optional[float] = None
    risk_score: Optional[float] = None


class RFQQuoteRequest(BaseModel):
    item_quotes: List[RFQQuoteItem] = Field(default_factory=list)
    vendor_id: Optional[str] = None
    quote_number: Optional[str] = None
    quote_currency: Optional[str] = None
    subtotal: Optional[float] = None
    freight: Optional[float] = None
    taxes: Optional[float] = None
    total: Optional[float] = None
    quote_status: Optional[str] = "received"
    response_status: Optional[str] = "received"
    quote_version: Optional[int] = 1
    acceptance_status: Optional[str] = "pending"
    incoterms: Optional[str] = None
    tax_assumptions: Dict[str, Any] = Field(default_factory=dict)
    duty_assumptions: Dict[str, Any] = Field(default_factory=dict)
    tier_pricing_json: Dict[str, Any] = Field(default_factory=dict)
    line_normalization_source: Optional[str] = None
    vendor_response_deadline: Optional[str] = None
    sent_at: Optional[str] = None
    received_at: Optional[str] = None
    expires_at: Optional[str] = None


class RFQSendRequest(BaseModel):
    vendor_ids: List[str] = Field(default_factory=list)
    vendor_response_deadline_days: int = 7
    notes: Optional[str] = None


class RFQSelectRequest(BaseModel):
    vendor_id: Optional[str] = None
    quote_id: Optional[str] = None
    reason: Optional[str] = None


class RFQRejectVendorRequest(BaseModel):
    vendor_id: Optional[str] = None
    quote_id: Optional[str] = None
    reason: Optional[str] = None


class RFQItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    bom_part_id: Optional[str] = None
    part_name: Optional[str] = None
    quantity: int = 1
    material: Optional[str] = None
    process: Optional[str] = None
    quoted_price: Optional[float] = None
    lead_time: Optional[float] = None
    drawing_required: Optional[bool] = False
    status: Optional[str] = None
    canonical_part_key: Optional[str] = None
    spec_summary: Dict[str, Any] = Field(default_factory=dict)


class RFQQuoteLineSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    quote_header_id: str
    rfq_item_id: str
    bom_part_id: str
    part_name: Optional[str] = None
    quantity: float = 1
    unit_price: Optional[float] = None
    line_currency: str = "USD"
    lead_time: Optional[float] = None
    availability_status: str = "unknown"
    compliance_status: str = "unknown"
    moq: Optional[float] = None
    risk_score: Optional[float] = None
    quote_version: int = 1
    normalization_source: Optional[str] = None
    tier_price_json: Dict[str, Any] = Field(default_factory=dict)
    tax_duty_assumptions: Dict[str, Any] = Field(default_factory=dict)
    line_payload: Dict[str, Any] = Field(default_factory=dict)


class RFQQuoteHeaderSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rfq_batch_id: str
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    quote_number: Optional[str] = None
    quote_status: str = "received"
    response_status: str = "received"
    quote_currency: str = "USD"
    incoterms: Optional[str] = None
    freight_terms: Optional[str] = None
    quote_version: int = 1
    acceptance_status: str = "pending"
    accepted_at: Optional[str] = None
    line_normalization_source: Optional[str] = None
    tax_duty_assumptions: Dict[str, Any] = Field(default_factory=dict)
    subtotal: Optional[float] = None
    freight: Optional[float] = None
    taxes: Optional[float] = None
    total: Optional[float] = None
    tax_assumptions: Dict[str, Any] = Field(default_factory=dict)
    duty_assumptions: Dict[str, Any] = Field(default_factory=dict)
    tier_pricing_json: Dict[str, Any] = Field(default_factory=dict)
    vendor_response_deadline: Optional[str] = None
    sent_at: Optional[str] = None
    received_at: Optional[str] = None
    expires_at: Optional[str] = None
    valid_until: Optional[str] = None
    response_payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    lines: List[RFQQuoteLineSchema] = Field(default_factory=list)


class RFQComparisonCellSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vendor_id: str
    vendor_name: str
    quote_header_id: Optional[str] = None
    price: Optional[float] = None
    lead_time: Optional[float] = None
    availability_status: str = "unknown"
    compliance_status: str = "unknown"
    moq: Optional[float] = None
    risk_score: Optional[float] = None
    quote_status: Optional[str] = None
    response_status: Optional[str] = None


class RFQComparisonRowSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rfq_item_id: str
    bom_part_id: str
    part_name: Optional[str] = None
    quantity: float = 1
    material: Optional[str] = None
    process: Optional[str] = None
    cells: Dict[str, RFQComparisonCellSchema] = Field(default_factory=dict)
    best_vendor_id: Optional[str] = None
    best_vendor_name: Optional[str] = None
    best_price: Optional[float] = None
    best_lead_time: Optional[float] = None


class RFQComparisonViewSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rfq_batch_id: str
    version: int = 1
    sort_by: str = "total_cost"
    filters_json: Dict[str, Any] = Field(default_factory=dict)
    comparison_json: Dict[str, Any] = Field(default_factory=dict)
    summary_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    vendors: List[Dict[str, Any]] = Field(default_factory=list)
    rows: List[RFQComparisonRowSchema] = Field(default_factory=list)


class RFQResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bom_id: Optional[str] = None
    project_id: Optional[str] = None
    status: str
    total_estimated_cost: Optional[float] = None
    total_final_cost: Optional[float] = None
    currency: str = "USD"
    notes: Optional[str] = None
    vendor_response_deadline: Optional[str] = None
    sent_at: Optional[str] = None
    received_at: Optional[str] = None
    expires_at: Optional[str] = None
    quote_status: Optional[str] = None
    response_status: Optional[str] = None
    selected_vendor_id: Optional[str] = None
    items: List[RFQItemSchema] = Field(default_factory=list)
    quotes: List[RFQQuoteHeaderSchema] = Field(default_factory=list)
    comparison: Optional[RFQComparisonViewSchema] = None


class TrackingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rfq_id: str
    stage: str
    status_message: Optional[str] = None
    progress_percent: int = 0


class FeedbackRequest(BaseModel):
    actual_cost: Optional[float] = None
    actual_lead_time: Optional[float] = None
    feedback_notes: Optional[str] = None