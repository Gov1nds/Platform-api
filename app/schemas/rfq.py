"""RFQ schemas."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class RFQCreateRequest(BaseModel):
    bom_id: str
    notes: Optional[str] = None


class RFQQuoteItem(BaseModel):
    part_name: str
    price: float
    lead_time: Optional[float] = None


class RFQQuoteRequest(BaseModel):
    item_quotes: List[RFQQuoteItem] = Field(default_factory=list)
    vendor_id: Optional[str] = None


class RFQItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    part_name: Optional[str] = None
    quantity: int = 1
    material: Optional[str] = None
    process: Optional[str] = None
    quoted_price: Optional[float] = None
    lead_time: Optional[float] = None


class RFQResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bom_id: Optional[str] = None
    status: str
    total_estimated_cost: Optional[float] = None
    total_final_cost: Optional[float] = None
    currency: str = "USD"
    items: List[RFQItemSchema] = Field(default_factory=list)
    notes: Optional[str] = None


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
