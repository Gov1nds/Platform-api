"""RFQ schemas."""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class RFQCreateRequest(BaseModel):
    bom_id: str
    notes: Optional[str] = None


class RFQItemSchema(BaseModel):
    part_name: Optional[str] = None
    quantity: int = 1
    material: Optional[str] = None
    process: Optional[str] = None
    quoted_price: Optional[float] = None
    lead_time: Optional[float] = None

    class Config:
        from_attributes = True


class RFQResponse(BaseModel):
    id: str
    bom_id: Optional[str] = None
    status: str
    total_estimated_cost: Optional[float] = None
    total_final_cost: Optional[float] = None
    currency: str = "USD"
    items: List[RFQItemSchema] = []
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class TrackingResponse(BaseModel):
    rfq_id: str
    stage: str
    status_message: Optional[str] = None
    progress_percent: int = 0

    class Config:
        from_attributes = True


class FeedbackRequest(BaseModel):
    actual_cost: Optional[float] = None
    actual_lead_time: Optional[float] = None
    feedback_notes: Optional[str] = None
