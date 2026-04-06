from __future__ import annotations
from pydantic import BaseModel
from datetime import datetime

class UserRegister(BaseModel):
    email:str; password:str; full_name:str=""; session_token:str|None=None
class UserLogin(BaseModel):
    email:str; password:str; session_token:str|None=None
class UserResponse(BaseModel):
    id:str; email:str; full_name:str; role:str="buyer"; is_active:bool=True; is_verified:bool=False
    model_config={"from_attributes":True}
class TokenResponse(BaseModel):
    access_token:str; token_type:str="bearer"; user:UserResponse; merge_result:dict={}
class VendorUserLogin(BaseModel):
    email:str; password:str
class VendorUserResponse(BaseModel):
    id:str; vendor_id:str; email:str; full_name:str; role:str
    model_config={"from_attributes":True}
class VendorTokenResponse(BaseModel):
    access_token:str; token_type:str="bearer"; user:VendorUserResponse

class BOMAnalyzeResponse(BaseModel):
    search_session_id:str; total_parts:int=0; analysis:dict={}; recommended_flow:str="search_session"
class BOMUploadResponse(BaseModel):
    bom_id:str; project_id:str; total_parts:int=0; status:str="analyzed"; analysis:dict={}

class ProjectResponse(BaseModel):
    id:str; bom_id:str; name:str; status:str; visibility:str; total_parts:int=0
    average_cost:float|None=None; cost_range_low:float|None=None; cost_range_high:float|None=None
    lead_time_days:float|None=None; decision_summary:str|None=None; file_name:str|None=None
    analyzer_report:dict={}; strategy:dict={}; events:list[dict]=[]; created_at:datetime|None=None
    model_config={"from_attributes":True}
class ProjectListResponse(BaseModel):
    items:list[ProjectResponse]=[]; total:int=0

class SearchSessionResponse(BaseModel):
    id:str; query_text:str|None=None; query_type:str; input_type:str; results_json:dict={}
    analysis_payload:dict={}; status:str; promoted_to:str|None=None; promoted_to_id:str|None=None
    created_at:datetime|None=None
    model_config={"from_attributes":True}
class SourcingCaseResponse(BaseModel):
    id:str; name:str; query_text:str|None=None; analysis_payload:dict={}; vendor_shortlist:list=[]
    status:str; promoted_to_project_id:str|None=None; created_at:datetime|None=None
    model_config={"from_attributes":True}

class VendorResponse(BaseModel):
    id:str; name:str; country:str|None=None; region:str|None=None; website:str|None=None
    contact_email:str|None=None; reliability_score:float=0.8; avg_lead_time_days:float|None=None
    certifications:list=[]; regions_served:list=[]; capacity_profile:dict={}; is_active:bool=True
    model_config={"from_attributes":True}
class VendorMatchResponse(BaseModel):
    vendor_id:str; vendor_name:str; rank:int; total_score:float; breakdown:dict={}
    explanation:str=""; explanation_json:dict={}; market_freshness:str=""
class VendorMatchListResponse(BaseModel):
    run_id:str; project_id:str; matches:list[VendorMatchResponse]=[]; total_considered:int=0

class RFQCreateRequest(BaseModel):
    bom_id:str; project_id:str; vendor_ids:list[str]=[]; notes:str=""; deadline:datetime|None=None
class RFQResponse(BaseModel):
    id:str; project_id:str|None=None; bom_id:str; status:str; notes:str|None=None
    deadline:datetime|None=None; items:list[dict]=[]; invitations:list[dict]=[]
    quotes:list[dict]=[]; created_at:datetime|None=None
    model_config={"from_attributes":True}

class QuoteSubmitRequest(BaseModel):
    rfq_batch_id:str=""; vendor_id:str=""; quote_number:str|None=None; currency:str="USD"
    incoterms:str|None=None; valid_until:datetime|None=None; lines:list[dict]=[]
    notes:str=""
class QuoteResponse(BaseModel):
    id:str; rfq_batch_id:str; vendor_id:str|None=None; quote_status:str; quote_version:int=1
    total:float|None=None; lines:list[dict]=[]; created_at:datetime|None=None
    model_config={"from_attributes":True}

class POCreateRequest(BaseModel):
    project_id:str; rfq_batch_id:str|None=None; vendor_id:str
    shipping_terms:str|None=None; payment_terms:str|None=None; line_items:list[dict]=[]
class POResponse(BaseModel):
    id:str; project_id:str; vendor_id:str|None=None; po_number:str|None=None
    status:str; total:float|None=None; currency:str="USD"; created_at:datetime|None=None
    model_config={"from_attributes":True}

class ShipmentCreateRequest(BaseModel):
    po_id:str; project_id:str|None=None; carrier:str|None=None; tracking_number:str|None=None
    origin:str|None=None; destination:str|None=None; eta:datetime|None=None
class MilestoneCreateRequest(BaseModel):
    shipment_id:str; milestone_type:str; location:str|None=None; notes:str|None=None; is_delay:bool=False
class ShipmentResponse(BaseModel):
    id:str; po_id:str; carrier:str|None=None; tracking_number:str|None=None; status:str
    eta:datetime|None=None; milestones:list[dict]=[]; created_at:datetime|None=None
    model_config={"from_attributes":True}

class ThreadCreateRequest(BaseModel):
    context_type:str; context_id:str; title:str|None=None
class MessageCreateRequest(BaseModel):
    thread_id:str; content:str; visibility:str="internal"; attachment_url:str|None=None
class ThreadResponse(BaseModel):
    id:str; context_type:str; context_id:str; title:str|None=None; created_at:datetime|None=None
    model_config={"from_attributes":True}
class MessageResponse(BaseModel):
    id:str; thread_id:str; sender_user_id:str|None=None; visibility:str; content:str
    created_at:datetime|None=None
    model_config={"from_attributes":True}

class ReportRequest(BaseModel):
    report_type:str; scope_type:str|None=None; scope_id:str|None=None; filters:dict={}
class ReportResponse(BaseModel):
    id:str; report_type:str; data_json:dict={}; summary_json:dict={}; created_at:datetime|None=None
    model_config={"from_attributes":True}
