"""
BOM Routes v3 — Full DB integration, HTTP analyzer bridge, guest/auth split.

CHANGES from v2:
  - Uses create_bom_from_analyzer() instead of local parsing
  - Transforms BOM Engine v3 output into section_2 format for strategy engine
  - Removed duplicate CSV parsing dependency
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.user import User
from app.models.bom import BOM, BOMStatus
from app.models.analysis import AnalysisResult, CostSavings
from app.schemas.bom import BOMUploadResponse, BOMUnlockRequest, BOMUnlockResponse
from app.utils.dependencies import get_current_user
from app.services import bom_service, analyzer_service, pricing_service, vendor_service
from app.services.strategy_service import build_strategy_output
from app.services.procurement_planner import generate_procurement_plan

logger = logging.getLogger("routes.bom")
router = APIRouter(prefix="/bom", tags=["bom"])


# ═══════════════════════════════════════════════════════════
# POST /upload — Main entry point for BOM analysis
# ═══════════════════════════════════════════════════════════

@router.post("/upload", response_model=BOMUploadResponse)
async def bom_upload(
    file: UploadFile = File(...),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Main BOM upload endpoint.
    Frontend calls ONLY this — never the BOM analyzer directly.

    Flow:
      1. Read file bytes
      2. Send file to BOM Engine → get normalized + classified components
      3. Store BOM + parts in DB from Engine output (NO local parsing)
      4. Run pricing + strategy + procurement on Platform API side
      5. Store AnalysisResult + CostSavings in DB
      6. Return preview (guest) or full report (authenticated)
    """

    # ── 1. Read file ──────────────────────────────────────────
    content = await file.read()
    filename = file.filename or "upload.csv"

    # ── 2. Call BOM Engine (parse + classify + specs ONLY) ────
    try:
        analyzer_output = await analyzer_service.call_analyzer(
            file_bytes=content,
            filename=filename,
            user_location=delivery_location,
            target_currency=target_currency,
        )
    except RuntimeError as e:
        logger.error(f"Analyzer call failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    # ── 3. Store BOM + parts from Engine output ───────────────
    vendor_service.seed_vendors(db)

    bom = bom_service.create_bom_from_analyzer(
        db,
        analyzer_output,
        file_name=filename,
        file_type=filename.rsplit(".", 1)[-1] if "." in filename else "csv",
        user_id=user.id if user else None,
    )

    # ── 4. Run pricing + strategy (Platform API intelligence) ─
    #
    # Transform BOM Engine components into the section_2 format
    # that strategy_service.build_strategy_output() expects.
    # This is a lightweight adapter — no logic duplication.
    #
    section_2 = _components_to_section_2(analyzer_output["components"])
    analysis_input = {"section_2_component_breakdown": section_2}

    # Fetch external pricing for standard parts
    parts = bom_service.get_bom_parts_as_dicts(db, bom.id)
    external_pricing = pricing_service.fetch_external_pricing(parts)

    # Enrich with DB-first pricing
    enriched = pricing_service.enrich_analysis_with_pricing(
        analysis_input, db, external_pricing
    )

    if priority not in ("cost", "speed"):
        priority = "cost"

    # Run global procurement strategy
    vendor_memories = vendor_service.get_vendor_memories(db)
    strategy = build_strategy_output(
        analysis_input,
        delivery_location,
        vendor_memories,
        pricing_history=[],
        external_pricing=external_pricing,
        db=db,
        priority=priority,
    )

    # Generate execution-ready procurement plan
    procurement = generate_procurement_plan(
        strategy, target_currency, max_suppliers=5
    )

    # ── 5. Store EVERYTHING in DB ─────────────────────────────
    ps = strategy.get("procurement_strategy", {})
    cs = ps.get("cost_summary", {})
    rec = strategy.get("recommended_strategy", {})
    cost_range = cs.get("range", [0, 0])

    analysis = AnalysisResult(
        bom_id=bom.id,
        user_id=user.id if user else None,
        raw_analyzer_output=analyzer_output,  # store raw Engine output
        strategy_output=strategy,
        enriched_output={
            "analyzer": enriched,
            "procurement_plan": procurement,
            "external_pricing": {k: v for k, v in external_pricing.items() if v},
            "priority": priority,
        },
        recommended_location=rec.get("location", ""),
        average_cost=cs.get("average", rec.get("average_cost", 0)),
        cost_range_low=cost_range[0] if len(cost_range) > 0 else 0,
        cost_range_high=cost_range[1] if len(cost_range) > 1 else 0,
        savings_percent=cs.get("savings_percent", rec.get("savings_percent", 0)),
        lead_time=rec.get("lead_time", 0),
        decision_summary=strategy.get("decision_summary", ""),
    )
    db.add(analysis)
    db.flush()

    # Cost savings record
    alt_strats = strategy.get("alternative_strategies", [])
    if alt_strats:
        alt = alt_strats[0]
        db.add(CostSavings(
            analysis_id=analysis.id,
            recommended_cost=cs.get("average", 0),
            alternative_cost=alt.get("total_cost", 0),
            savings_percent=cs.get("savings_percent", 0),
            savings_value=cs.get("savings_value", 0),
        ))

    bom.status = BOMStatus.analyzed.value
    db.commit()

    # ── 6. Return based on auth state ─────────────────────────
    if user:
        return BOMUploadResponse(
            bom_id=bom.id,
            session_token="",
            total_parts=bom.total_parts,
            status=bom.status,
            preview=_build_full_response(
                analysis_input, strategy, procurement, bom, priority
            ),
        )
    else:
        return BOMUploadResponse(
            bom_id=bom.id,
            session_token=bom.session_token,
            total_parts=bom.total_parts,
            status=bom.status,
            preview=_build_preview_response(
                analysis_input, strategy, bom, priority
            ),
        )


# ═══════════════════════════════════════════════════════════
# POST /unlock — Retrieve full stored result (no recompute)
# ═══════════════════════════════════════════════════════════

@router.post("/unlock", response_model=BOMUnlockResponse)
def bom_unlock(
    body: BOMUnlockRequest,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Unlock full stored result. NO recomputation.
    Guest uses session_token; logged-in user uses JWT.
    If guest later logs in, BOM ownership is transferred.
    """
    bom = db.query(BOM).filter(BOM.id == body.bom_id).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")

    # Auth check
    authorized = False
    if user and bom.user_id == user.id:
        authorized = True
    elif body.session_token and bom.session_token == body.session_token:
        authorized = True
        # Transfer ownership if guest now logged in
        if user and not bom.user_id:
            bom.user_id = user.id
            db.commit()

    if not authorized:
        raise HTTPException(status_code=403, detail="Not authorized")

    analysis = db.query(AnalysisResult).filter(
        AnalysisResult.bom_id == bom.id
    ).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return BOMUnlockResponse(
        bom_id=bom.id,
        full_report=analysis.enriched_output or analysis.raw_analyzer_output or {},
        strategy=analysis.strategy_output or {},
    )


# ═══════════════════════════════════════════════════════════
# Adapter: BOM Engine v3 output → strategy_service input
# ═══════════════════════════════════════════════════════════

def _components_to_section_2(components: list) -> list:
    """
    Transform BOM Engine v3's flat component list into the
    section_2_component_breakdown format that strategy_service expects.

    This is a pure data mapping — no business logic, no duplication.
    The strategy_service.evaluate_part() function reads these fields.
    """
    section_2 = []
    for comp in components:
        section_2.append({
            # Identity
            "item_id": comp.get("item_id", ""),
            "description": comp.get("description", ""),
            "quantity": comp.get("quantity", 1),
            "material": comp.get("material", ""),
            "mpn": comp.get("mpn", ""),
            "manufacturer": comp.get("manufacturer", ""),
            "notes": comp.get("notes", ""),
            # Classification
            "category": comp.get("category", "standard"),
            "classification_confidence": comp.get("classification_confidence", 0),
            # Manufacturing attributes
            "geometry": comp.get("geometry"),
            "tolerance": comp.get("tolerance"),
            "material_form": comp.get("material_form"),
            "secondary_ops": comp.get("secondary_ops", []),
            # Specs (used by pricing for parametric estimation)
            "specs": comp.get("specs", {}),
        })
    return section_2


# ═══════════════════════════════════════════════════════════
# Response builders — preview vs full
# ═══════════════════════════════════════════════════════════

def _build_preview_response(analyzer, strategy, bom, priority):
    """
    Guest response: limited data only.
    Shows enough to demonstrate value, hides detailed breakdown.
    """
    ps = strategy.get("procurement_strategy", {})
    rec = strategy.get("recommended_strategy", {})
    cs = ps.get("cost_summary", {})

    return {
        "is_preview": True,
        # Shown to guest
        "cost_range": cs.get("range", [0, 0]),
        "total_cost": cs.get("average", 0),
        "lead_time": ps.get("timeline", {}),
        "risk_level": ps.get("risk_analysis", {}).get("risk_level", "MEDIUM"),
        "total_parts": bom.total_parts,
        "priority": priority,
        "basic_processes": (rec.get("reasons", []))[:3],
        "region_distribution": strategy.get("region_distribution", {}),
        "decision_summary": strategy.get("decision_summary", "")[:200],
        # CTA
        "unlock_message": (
            "Sign up to see full BOM breakdown, "
            "cost optimization, and procurement plan"
        ),
    }


def _build_full_response(analyzer, strategy, procurement, bom, priority):
    """
    Authenticated response: everything.
    Contains strategy, procurement plan, and all analysis data.
    """
    return {
        "is_preview": False,
        "analyzer_report": analyzer,
        "strategy": strategy,
        "procurement_plan": procurement,
        "total_parts": bom.total_parts,
        "priority": priority,
    }