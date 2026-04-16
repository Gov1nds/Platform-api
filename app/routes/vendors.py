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


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Vendor Intelligence endpoints (additive; existing routes preserved)
# ═════════════════════════════════════════════════════════════════════════════

from typing import Any
from fastapi import Body, File, Form, UploadFile
from pydantic import BaseModel

from app.models.vendor import (
    VendorLocation as _VendorLocation,
    VendorExportCapability as _VendorExportCapability,
    VendorLeadTimeBand as _VendorLeadTimeBand,
    VendorCommunicationScore as _VendorCommunicationScore,
    VendorTrustTier as _VendorTrustTier,
    VendorPerformanceSnapshot as _VendorPerformanceSnapshot,
)
from app.models.market_intelligence import MarketAnomalyEvent as _MarketAnomalyEvent
from app.models.feedback import LearningEvent as _LearningEvent
from app.schemas.recommendation import VendorIntelligenceProfile as _VendorIntelligenceProfile
from app.services.vendor_intelligence_service import vendor_intelligence_service as _vi
from app.services.ingestion.vendor_csv_ingestion_service import (
    vendor_csv_ingestion_service as _csv_ingest,
)
from app.services.ingestion.catalog_ingestion_service import (
    catalog_ingestion_service as _catalog_ingest,
)
from app.services.learning.feedback_loop_service import feedback_loop_service as _fb


def _snapshot_to_dict(snap):
    if snap is None:
        return None
    return {
        "snapshot_date": snap.snapshot_date.isoformat() if snap.snapshot_date else None,
        "total_pos": int(snap.total_pos or 0),
        "on_time_delivery_pct": float(snap.on_time_delivery_pct) if snap.on_time_delivery_pct is not None else None,
        "quality_pass_pct": float(snap.quality_pass_pct) if snap.quality_pass_pct is not None else None,
        "avg_response_time_hours": float(snap.avg_response_time_hours) if snap.avg_response_time_hours is not None else None,
        "quote_win_rate": float(snap.quote_win_rate) if snap.quote_win_rate is not None else None,
    }


@router.get("/{vendor_id}/intelligence", response_model=_VendorIntelligenceProfile)
def vendor_intelligence_profile(vendor_id: str, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, "Vendor not found")

    trust = (
        db.query(_VendorTrustTier).filter(_VendorTrustTier.vendor_id == v.id).first()
    )
    comm = (
        db.query(_VendorCommunicationScore)
        .filter(_VendorCommunicationScore.vendor_id == v.id)
        .first()
    )
    snap = (
        db.query(_VendorPerformanceSnapshot)
        .filter(_VendorPerformanceSnapshot.vendor_id == v.id)
        .order_by(_VendorPerformanceSnapshot.snapshot_date.desc())
        .first()
    )
    anomalies = (
        db.query(_MarketAnomalyEvent)
        .filter(_MarketAnomalyEvent.vendor_id == v.id)
        .order_by(_MarketAnomalyEvent.detected_at.desc())
        .limit(25)
        .all()
    )

    return _VendorIntelligenceProfile(
        vendor_id=v.id,
        vendor_name=v.name or "",
        trust_tier=v.trust_tier or "UNVERIFIED",
        trust_tier_details={
            "data_completeness_score": float(trust.data_completeness_score) if trust else 0.0,
            "reliability_score": float(trust.reliability_score) if trust else 0.0,
            "evidence_count": int(trust.evidence_count or 0) if trust else 0,
            "computed_at": trust.computed_at.isoformat() if trust and trust.computed_at else None,
        },
        profile_flags=list(v.profile_flags or []),
        missing_required_fields=list(v.missing_required_fields or []),
        validation_errors=list(v.validation_errors or []),
        locations=[
            {
                "id": loc.id, "label": loc.label, "city": loc.city,
                "state_province": loc.state_province, "country_iso2": loc.country_iso2,
                "is_primary": bool(loc.is_primary),
                "is_export_office": bool(loc.is_export_office),
                "geo_region_tag": loc.geo_region_tag,
            }
            for loc in (v.locations or [])
        ],
        export_capabilities=[
            {
                "id": ec.id, "hs_code": ec.hs_code, "hs_description": ec.hs_description,
                "export_country_iso2": ec.export_country_iso2,
                "supported_incoterms": list(ec.supported_incoterms or []),
            }
            for ec in (v.export_capabilities or [])
        ],
        lead_time_bands=[
            {
                "id": b.id, "category_tag": b.category_tag, "material_family": b.material_family,
                "moq": str(b.moq) if b.moq is not None else None,
                "moq_unit": b.moq_unit,
                "lead_time_min_days": b.lead_time_min_days,
                "lead_time_max_days": b.lead_time_max_days,
                "lead_time_typical_days": float(b.lead_time_typical_days) if b.lead_time_typical_days is not None else None,
                "confidence": float(b.confidence or 0),
                "source": b.source,
            }
            for b in (v.lead_time_bands or [])
        ],
        communication_score=(
            {
                "rfq_response_rate": float(comm.rfq_response_rate) if comm and comm.rfq_response_rate is not None else None,
                "avg_response_time_hours": float(comm.avg_response_time_hours) if comm and comm.avg_response_time_hours is not None else None,
                "communication_quality_score": float(comm.communication_quality_score) if comm and comm.communication_quality_score is not None else None,
                "total_rfqs_sent": int(comm.total_rfqs_sent or 0) if comm else 0,
                "total_rfqs_responded": int(comm.total_rfqs_responded or 0) if comm else 0,
            }
            if comm else None
        ),
        performance_snapshot=_snapshot_to_dict(snap),
        anomaly_history=[
            {
                "id": a.id, "anomaly_type": a.anomaly_type, "severity": a.severity,
                "observed": str(a.observed_value) if a.observed_value is not None else None,
                "detected_at": a.detected_at.isoformat() if a.detected_at else None,
                "reviewed": bool(a.reviewed),
            }
            for a in anomalies
        ],
        primary_category_tag=v.primary_category_tag,
        secondary_category_tags=list(v.secondary_category_tags or []),
        dedup_fingerprint=v.dedup_fingerprint,
        last_validated_at=v.last_validated_at,
    )


@router.post("/{vendor_id}/validate")
def vendor_validate(vendor_id: str, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, "Vendor not found")
    validation = _vi.validate_vendor_profile(vendor_id, db)
    tier = _vi.compute_trust_tier(vendor_id, db)
    _vi.refresh_vendor_fingerprint(db, v)
    db.commit()
    return {
        "vendor_id": vendor_id,
        "validation_ok": validation.ok,
        "errors": list(validation.errors),
        "warnings": list(validation.warnings),
        "trust_tier": tier.to_dict(),
        "dedup_fingerprint": v.dedup_fingerprint,
    }


@router.get("/duplicates")
def vendor_duplicates(
    min_similarity: float = Query(0.85, ge=0.0, le=1.0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    vendors = (
        db.query(Vendor)
        .filter(
            Vendor.is_active.is_(True),
            Vendor.deleted_at.is_(None),
            Vendor.merged_into_vendor_id.is_(None),
        )
        .limit(limit)
        .all()
    )
    pairs: dict[tuple[str, str], dict] = {}
    for v in vendors:
        for cand in _vi.find_duplicate_candidates(v.id, db, min_similarity=min_similarity):
            a, b = sorted([cand.primary_vendor_id, cand.candidate_vendor_id])
            pairs[(a, b)] = {
                "vendor_a_id": a,
                "vendor_b_id": b,
                "candidate_name": cand.candidate_name,
                "similarity": cand.similarity,
                "match_reason": cand.match_reason,
            }
    return {"pairs": list(pairs.values()), "total": len(pairs)}


class _MergeRequest(BaseModel):
    primary_id: str
    duplicate_id: str


@router.post("/merge")
def vendor_merge(body: _MergeRequest, db: Session = Depends(get_db)):
    try:
        summary = _vi.merge_vendor_duplicates(body.primary_id, body.duplicate_id, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return summary


@router.post("/import/csv")
def vendor_import_csv(
    file: UploadFile = File(...),
    org_id: str | None = Form(default=None),
    created_by_user_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    content = file.file.read()
    result = _csv_ingest.ingest_vendor_csv(
        file_content=content,
        org_id=org_id,
        created_by_user_id=created_by_user_id,
        db=db,
        file_name=file.filename,
    )
    db.commit()
    return result.to_dict()


@router.post("/{vendor_id}/catalog")
def vendor_ingest_catalog(
    vendor_id: str,
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    items = body.get("items") or body.get("catalog") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "body.items must be a non-empty list")
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, "Vendor not found")
    result = _catalog_ingest.ingest_catalog_for_vendor(vendor_id, items, db)
    db.commit()
    return result.to_dict()


class _OverrideRequest(BaseModel):
    project_id: str
    bom_part_id: str | None = None
    canonical_part_key: str | None = None
    recommended_vendor_id: str | None = None
    override_vendor_id: str | None = None
    override_reason: str | None = None
    strategy_at_time: str | None = None
    score_at_time: float | None = None
    override_metadata: dict[str, Any] | None = None


@router.post("/recommendation-override")
def vendor_recommendation_override(
    body: _OverrideRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rec = _fb.record_user_override(
        project_id=body.project_id,
        bom_part_id=body.bom_part_id,
        canonical_part_key=body.canonical_part_key,
        recommended_vendor_id=body.recommended_vendor_id,
        override_vendor_id=body.override_vendor_id,
        override_reason=body.override_reason,
        override_by_user_id=user.id,
        db=db,
        strategy_at_time=body.strategy_at_time,
        score_at_time=body.score_at_time,
        override_metadata=body.override_metadata or {},
    )
    db.commit()
    return {
        "override_id": rec.id,
        "project_id": rec.project_id,
        "recommended_vendor_id": rec.recommended_vendor_id,
        "override_vendor_id": rec.override_vendor_id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


@router.get("/{vendor_id}/override-patterns")
def vendor_override_patterns(vendor_id: str, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, "Vendor not found")
    analysis = _fb.get_override_pattern_analysis(vendor_id, db)
    return analysis.to_dict()


@router.get("/learning-events")
def vendor_learning_events(
    human_review_required: bool = Query(default=True),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(_LearningEvent).filter(
        _LearningEvent.human_review_required == human_review_required
    ).order_by(_LearningEvent.created_at.desc())
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "vendor_id": e.vendor_id,
                "canonical_part_key": e.canonical_part_key,
                "trigger": e.trigger,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "evidence_count_at_time": e.evidence_count_at_time,
                "human_review_required": bool(e.human_review_required),
                "human_review_completed": bool(e.human_review_completed),
                "notes": e.notes,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in items
        ],
    }
