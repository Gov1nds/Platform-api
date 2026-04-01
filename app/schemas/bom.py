"""BOM schemas."""
from pydantic import BaseModel
from typing import Optional, Dict, Any


class BOMPartSchema(BaseModel):
    part_name: Optional[str] = None
    material: Optional[str] = None
    quantity: int = 1
    geometry_type: Optional[str] = None
    dimensions: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    manufacturer: Optional[str] = None
    mpn: Optional[str] = None


class BOMLifecycleState(BaseModel):
    guest_bom_id: Optional[str] = None
    project_id: Optional[str] = None
    session_token: Optional[str] = None
    analysis_status: str = "guest_preview"
    report_visibility_level: str = "preview"
    unlock_status: str = "locked"
    workspace_route: Optional[str] = None

    class Config:
        from_attributes = True


class BOMUploadResponse(BaseModel):
    bom_id: str
    guest_bom_id: Optional[str] = None
    session_token: str = ""
    analysis_status: str = "guest_preview"
    report_visibility_level: str = "preview"
    unlock_status: str = "locked"
    project_id: Optional[str] = None
    workspace_route: Optional[str] = None
    total_parts: int
    status: str
    analysis_lifecycle: Optional[BOMLifecycleState] = None
    preview: Dict[str, Any]

    class Config:
        from_attributes = True


class BOMUnlockRequest(BaseModel):
    bom_id: str
    session_token: Optional[str] = None


class BOMUnlockResponse(BaseModel):
    bom_id: str
    guest_bom_id: Optional[str] = None
    session_token: Optional[str] = None
    analysis_status: str = "authenticated_unlocked"
    report_visibility_level: str = "full"
    unlock_status: str = "unlocked"
    project_id: Optional[str] = None
    workspace_route: Optional[str] = None
    analysis_lifecycle: Optional[BOMLifecycleState] = None
    full_report: Dict[str, Any]
    strategy: Dict[str, Any]
    procurement_plan: Dict[str, Any] = {}

    class Config:
        from_attributes = True