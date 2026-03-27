"""Drawing schemas."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class DrawingUploadResponse(BaseModel):
    id: str
    rfq_id: str
    part_name: Optional[str] = None
    original_filename: str
    file_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class DrawingListResponse(BaseModel):
    rfq_id: str
    drawings: List[DrawingUploadResponse]
    total: int
