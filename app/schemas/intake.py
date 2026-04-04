"""Universal intake schemas."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class IntakeInputType(str, Enum):
    auto = "auto"
    bom = "bom"
    item = "item"
    component = "component"
    material = "material"
    voice = "voice"
    free_text = "free_text"
    file = "file"

class ProcurementMode(str, Enum):
    auto = "auto"
    quick_catalog = "quick_catalog"
    guided_project = "guided_project"

class IntakeIntent(str, Enum):
    auto = "auto"
    source = "source"
    compare = "compare"
    rfq = "rfq"
    price_check = "price_check"
    vendor_search = "vendor_search"
    deep_search = "deep_search"
    research_product = "research_product"


class IntakeStatus(str, Enum):
    received = "received"
    parsed = "parsed"
    normalized = "normalized"
    analyzed = "analyzed"
    project_created = "project_created"
    catalog_ready = "catalog_ready"
    completed = "completed"
    failed = "failed"


class IntakeItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int = 1
    raw_text: str = ""
    item_name: str = ""
    category: str = "standard"
    material: Optional[str] = None
    process: Optional[str] = None
    quantity: float = 1.0
    unit: Optional[str] = None
    specs: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    warnings: List[str] = Field(default_factory=list)


class IntakeSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    namespace: str
    idempotency_key: str
    request_hash: str

    user_id: Optional[str] = None
    guest_session_id: Optional[str] = None
    session_token: Optional[str] = None

    input_type: str
    intent: str
    source_channel: str

    raw_input_text: Optional[str] = None
    normalized_text: Optional[str] = None
    voice_transcript: Optional[str] = None

    source_file_name: Optional[str] = None
    source_file_type: Optional[str] = None
    source_file_size: Optional[int] = None
    source_file_path: Optional[str] = None

    audio_file_name: Optional[str] = None
    audio_file_type: Optional[str] = None
    audio_file_size: Optional[int] = None
    audio_file_path: Optional[str] = None

    delivery_location: Optional[str] = None
    target_currency: Optional[str] = None
    priority: Optional[str] = None

    status: str
    parse_status: str
    analysis_status: str
    workflow_status: str

    confidence_score: float
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    parsed_payload: Dict[str, Any] = Field(default_factory=dict)
    normalized_payload: Dict[str, Any] = Field(default_factory=dict)
    analysis_payload: Dict[str, Any] = Field(default_factory=dict)
    preview_payload: Dict[str, Any] = Field(default_factory=dict)

    bom_id: Optional[str] = None
    analysis_id: Optional[str] = None
    project_id: Optional[str] = None

    items: List[IntakeItemSchema] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class IntakeParseRequest(BaseModel):
    raw_input_text: Optional[str] = None
    input_type: IntakeInputType = IntakeInputType.auto
    intent: IntakeIntent = IntakeIntent.auto
    delivery_location: str = "India"
    target_currency: str = "USD"
    priority: str = "cost"
    session_token: Optional[str] = None
    voice_transcript: Optional[str] = None
    source_channel: str = "web"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    purchase_mode: ProcurementMode = ProcurementMode.auto
    project_creation_mode: ProcurementMode = ProcurementMode.auto


class IntakeSubmitRequest(IntakeParseRequest):
    async_finalize: bool = True


class IntakeParseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intake_session: IntakeSessionSchema
    session_token: Optional[str] = None
    guest_session_id: Optional[str] = None
    bom_id: Optional[str] = None
    project_id: Optional[str] = None
    workspace_route: Optional[str] = None
    analysis_status: str = "pending"
    report_visibility_level: str = "preview"
    unlock_status: str = "locked"
    input_type: str
    intent: str
    normalized_text: str
    normalized_items: List[IntakeItemSchema] = Field(default_factory=list)
    parsed_summary: Dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    purchase_mode: str = "auto"
    item_count: int = 0
    recommended_flow: str = "project"
    should_create_project: bool = True
    quick_actions: List[str] = Field(default_factory=list)


class IntakeSubmitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intake_session: IntakeSessionSchema
    bom_id: Optional[str] = None
    project_id: Optional[str] = None
    analysis_id: Optional[str] = None
    workspace_route: Optional[str] = None
    analysis_status: str = "pending"
    report_visibility_level: str = "preview"
    unlock_status: str = "locked"
    normalized_items: List[IntakeItemSchema] = Field(default_factory=list)
    analysis_lifecycle: Dict[str, Any] = Field(default_factory=dict)
    preview: Dict[str, Any] = Field(default_factory=dict)
    strategy: Dict[str, Any] = Field(default_factory=dict)
    procurement_plan: Dict[str, Any] = Field(default_factory=dict)
    parsed_summary: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    purchase_mode: str = "auto"
    item_count: int = 0
    recommended_flow: str = "project"
    should_create_project: bool = True
    quick_actions: List[str] = Field(default_factory=list)

class IntakeSessionListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[IntakeSessionSchema] = Field(default_factory=list)
    total: int = 0