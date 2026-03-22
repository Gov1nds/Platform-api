"""BOM schemas."""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class BOMPartSchema(BaseModel):
    part_name: Optional[str] = None
    material: Optional[str] = None
    quantity: int = 1
    geometry_type: Optional[str] = None
    dimensions: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    manufacturer: Optional[str] = None
    mpn: Optional[str] = None


class BOMUploadResponse(BaseModel):
    bom_id: str
    session_token: str
    total_parts: int
    status: str
    preview: Dict[str, Any]

    class Config:
        from_attributes = True


class BOMUnlockRequest(BaseModel):
    bom_id: str
    session_token: Optional[str] = None


class BOMUnlockResponse(BaseModel):
    bom_id: str
    full_report: Dict[str, Any]
    strategy: Dict[str, Any]
