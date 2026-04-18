"""Chat & offer schemas (Blueprint §14.3, C24)."""
from pydantic import BaseModel
from typing import Literal, Optional

class OfferPayload(BaseModel):
    offer_type: Literal["price", "lead_time", "qty", "combined"]
    proposed_value: dict
    original_value: dict
    context_quote_id: Optional[str] = None
    context_line_id: Optional[str] = None
    expires_at: Optional[str] = None
    accepted: Optional[bool] = None
    responded_at: Optional[str] = None

class MessageCreateRequest(BaseModel):
    thread_id: str
    message_type: Literal["text", "file", "offer", "status_update", "system"]
    message_text: str = ""
    attachment_url: Optional[str] = None
    offer_payload_json: Optional[OfferPayload] = None
    idempotency_key: Optional[str] = None
