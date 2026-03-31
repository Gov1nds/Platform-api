"""BOM routes — upload, analyze, unlock, and project snapshot creation.
Updated for PostgreSQL schema.
"""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.bom import BOM
from app.models.analysis import AnalysisResult
from app.schemas.bom import BOMUploadResponse, BOMUnlockRequest, BOMUnlockResponse
from app.utils.dependencies import get_current_user
from app.services import bom_service, analyzer_service, pricing_service, vendor_service, project_service
from app.services.strategy_service import build_strategy_output
from app.services.procurement_planner import generate_procurement_plan
from app.services import resolver_service
from app.services import review_service

logger = logging.getLogger("routes.bom")
router = APIRouter(prefix="/bom", tags=["bom"])


@router.post("/upload", response_model=BOMUploadResponse)
async def bom_upload(
    file: UploadFile = File(...),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    user: Optional[User] = Depends(get_current_user),
    session_token: str = Form(None),
    db: Session = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or "upload.csv"

    try:
        analyzer_output = await analyzer_service.call_analyzer(
            file_bytes=content, filename=filename,
            user_location=delivery_location, target_currency=target_currency,
        )
    except RuntimeError as e:
        logger.error("Analyzer call failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

    # vendor_service.seed_vendors already runs at startup — no need to re-seed per request

    bom = bom_service.create_bom_from_analyzer(
        db, analyzer_output,
        file_name=filename,
        file_type=filename.rsplit(".", 1)[-1] if "." in filename else "csv",
        user_id=user.id if user else None,
        session_token=session_token,
    )

    v2_report = analyzer_output.get("_v2_full_report")
    if v2_report and "section_2_component_breakdown" in v2_report:
        strategy_input = v2_report
    else:
        strategy_input = {"section_2_component_breakdown": _components_to_section_2(analyzer_output.get("components", []))}

    parts = bom_service.get_bom_parts_as_dicts(db, bom.id)
    external_pricing = pricing_service.fetch_external_pricing(parts)
    enriched = pricing_service.enrich_analysis_with_pricing(strategy_input, db, external_pricing)

    if priority not in ("cost", "speed"):
        priority = "cost"

    vendor_memories = vendor_service.get_vendor_memories(db)
    strategy = build_strategy_output(
        strategy_input, delivery_location, vendor_memories,
        pricing_history=[], external_pricing=external_pricing,
        db=db, priority=priority, target_currency=target_currency,
    )
    procurement = generate_procurement_plan(strategy, target_currency, max_suppliers=5)

    ps = strategy.get("procurement_strategy", {})
    cs = ps.get("cost_summary", {})
    rec = strategy.get("recommended_strategy", {})
    cost_range = cs.get("range", [0, 0])

    analysis = AnalysisResult(
        bom_id=bom.id,
        user_id=user.id if user else None,
        guest_session_id=bom.guest_session_id,
        raw_analyzer_output=analyzer_output,
        structured_output={
            "strategy": strategy,
            "enriched": {
                "analyzer": enriched, "procurement_plan": procurement,
                "external_pricing": {k: v for k, v in external_pricing.items() if v},
                "priority": priority,
            },
        },
        recommended_location=rec.get("location", ""),
        average_cost=cs.get("average", rec.get("average_cost", 0)),
        cost_range_low=cost_range[0] if len(cost_range) > 0 else 0,
        cost_range_high=cost_range[1] if len(cost_range) > 1 else 0,
        savings_percent=cs.get("savings_percent", rec.get("savings_percent", 0)),
        lead_time_days=rec.get("lead_time", 0),
        decision_summary=strategy.get("decision_summary", ""),
        source_version=analyzer_output.get("_meta", {}).get("version", "unknown"),
    )
    db.add(analysis)
    db.flush()

    bom.status = "analyzed"

    project = project_service.upsert_project_from_analysis(
        db, bom=bom, analysis=analysis, analyzer_output=analyzer_output,
        strategy=strategy, procurement=procurement,
    )

    # Resolver: match BOM parts against canonical master and learn
    match_results = []
    try:
        source_file = bom.source_file_name or filename
        match_results = resolver_service.resolve_and_learn(db, parts, bom.id, source_file=source_file)
        resolver_service.update_bom_parts_with_matches(db, bom.id, match_results, parts)
    except Exception as e:
        logger.warning(f"Resolver failed (non-fatal): {e}")

    # Create review queue items for unresolved/review-needed parts
    try:
        if match_results:
            review_service.create_review_items_from_resolver(db, bom.id, match_results, parts)
    except Exception as e:
        logger.warning(f"Review queue creation failed (non-fatal): {e}")

    db.commit()
    db.refresh(bom)
    db.refresh(analysis)
    db.refresh(project)

    if user:
        return BOMUploadResponse(
            bom_id=bom.id,
            session_token="",
            total_parts=bom.total_parts,
            status=bom.status,
            preview=_build_authenticated_preview(project, analysis, strategy, procurement),
        )

    return BOMUploadResponse(
        bom_id=bom.id,
        session_token=bom.session_token or "",
        total_parts=bom.total_parts,
        status=bom.status,
        preview=project_service.build_guest_preview(project),
    )


@router.post("/unlock", response_model=BOMUnlockResponse)
def bom_unlock(
    body: BOMUnlockRequest,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bom = db.query(BOM).filter(BOM.id == body.bom_id).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")

    analysis = db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
    project = project_service.get_project_by_bom_id(db, bom.id)

    authorized = False
    if user and bom.uploaded_by_user_id == user.id:
        authorized = True
    elif body.session_token:
        from app.models.user import GuestSession
        gs = db.query(GuestSession).filter(
            GuestSession.session_token == body.session_token,
            GuestSession.id == bom.guest_session_id,
        ).first()
        if gs:
            authorized = True
            if user and not bom.uploaded_by_user_id:
                bom.uploaded_by_user_id = user.id
                if project:
                    project.user_id = user.id
                if analysis:
                    analysis.user_id = user.id
                db.commit()

    if not authorized:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not analysis or not project:
        raise HTTPException(status_code=404, detail="Analysis not found")

    full_report = project.analyzer_report or {}
    return BOMUnlockResponse(
        bom_id=bom.id,
        full_report=full_report,
        strategy=project.strategy or analysis.strategy_output or {},
    )


def _components_to_section_2(components: list) -> list:
    section_2 = []
    for comp in components:
        section_2.append({
            "item_id": comp.get("item_id", ""),
            "description": comp.get("description", ""),
            "quantity": comp.get("quantity", 1),
            "material": comp.get("material", ""),
            "mpn": comp.get("mpn", ""),
            "manufacturer": comp.get("manufacturer", ""),
            "notes": comp.get("notes", ""),
            "category": comp.get("category", "standard"),
            "classification_confidence": comp.get("classification_confidence", 0),
            "geometry": comp.get("geometry"),
            "tolerance": comp.get("tolerance"),
            "material_form": comp.get("material_form"),
            "secondary_ops": comp.get("secondary_ops", []),
            "specs": comp.get("specs", {}),
            "procurement_class": comp.get("procurement_class", "catalog_purchase"),
            "rfq_required": comp.get("rfq_required", False),
            "drawing_required": comp.get("drawing_required", False),
            "is_custom": comp.get("is_custom", False),
            "part_type": comp.get("part_type", "standard"),
        })
    return section_2


def _build_authenticated_preview(project, analysis, strategy, procurement):
    return {
        "is_preview": False,
        "project_id": project.id,
        "bom_id": project.bom_id,
        "analyzer_report": project.analyzer_report or {},
        "strategy": project.strategy or strategy,
        "procurement_plan": project.procurement_plan or procurement,
        "total_parts": project.total_parts,
        "priority": "cost",
        "currency": project.currency or strategy.get("currency", "USD"),
    }