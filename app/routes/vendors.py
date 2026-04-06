from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.vendor import Vendor, VendorCapability, VendorMatchRun, VendorMatch
from app.models.project import Project
from app.models.bom import BOMPart
from app.schemas import VendorResponse, VendorMatchListResponse, VendorMatchResponse
from app.utils.dependencies import require_user, require_project_owner
from app.services.scoring.vendor_scorer import rank_vendors, load_market_context

router = APIRouter(prefix="/vendors", tags=["Vendors"])

@router.get("")
def list_vendors(search:str=Query(""), limit:int=Query(50,ge=1,le=200), db:Session=Depends(get_db)):
    q = db.query(Vendor).filter(Vendor.is_active==True)
    if search: q = q.filter(Vendor.name.ilike(f"%{search}%"))
    return [VendorResponse.model_validate(v) for v in q.limit(limit).all()]

@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(vendor_id:str, db:Session=Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id==vendor_id).first()
    if not v: raise HTTPException(404,"Vendor not found")
    return VendorResponse.model_validate(v)

@router.get("/match/run", response_model=VendorMatchListResponse)
def match_vendors(project_id:str=Query(...), user:User=Depends(require_user), db:Session=Depends(get_db)):
    project = require_project_owner(project_id, db, user)
    parts = db.query(BOMPart).filter(BOMPart.bom_id==project.bom_id).all()
    processes = set(); materials = set(); total_qty = 0
    for p in parts:
        if p.procurement_class and p.procurement_class != "unknown": processes.add(p.procurement_class)
        if p.material: materials.add(p.material)
        total_qty += float(p.quantity or 0)
    delivery_region = (project.project_metadata or {}).get("delivery_region","")
    requirements = {"processes":list(processes),"materials":list(materials),"total_quantity":total_qty,
        "delivery_region":delivery_region,"required_certifications":[],"target_lead_time_days":30}
    market_ctx = load_market_context(db, delivery_region, "USD")
    market_ctx["market_median_price"] = None

    vendors = db.query(Vendor).filter(Vendor.is_active==True).all()
    vdicts = []
    for v in vendors:
        caps = db.query(VendorCapability).filter(VendorCapability.vendor_id==v.id,VendorCapability.is_active==True).all()
        vdicts.append({"id":v.id,"name":v.name,"reliability_score":float(v.reliability_score) if v.reliability_score else 0.5,
            "avg_lead_time_days":float(v.avg_lead_time_days) if v.avg_lead_time_days else None,
            "regions_served":v.regions_served or[],"certifications":v.certifications or[],
            "capacity_profile":v.capacity_profile or{},"capabilities":[{"process":c.process,"material_family":c.material_family} for c in caps]})

    scored = rank_vendors(vdicts, requirements, market_ctx)
    run = VendorMatchRun(project_id=project.id, user_id=user.id, filters_json=requirements,
        weights_json={}, total_vendors_considered=len(vdicts), total_matches=len(scored))
    db.add(run); db.flush()
    for s in scored:
        db.add(VendorMatch(match_run_id=run.id, project_id=project.id, vendor_id=s["vendor_id"],
            rank=s["rank"], score=s["total_score"], score_breakdown=s["breakdown"],
            explanation=s["explanation"], explanation_json=s["explanation_json"]))
    db.commit()
    return VendorMatchListResponse(run_id=run.id, project_id=project.id,
        matches=[VendorMatchResponse(**s) for s in scored], total_considered=len(vdicts))
