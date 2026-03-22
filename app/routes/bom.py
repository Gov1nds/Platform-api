"""BOM routes — full DB integration, HTTP analyzer bridge, guest/auth split."""
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
      2. Parse locally → store BOM + parts in DB
      3. Forward raw file to BOM Analyzer via HTTP
      4. Run strategy engine on analyzer output
      5. Store AnalysisResult + CostSavings in DB
      6. Return preview (guest) or full report (authenticated)
    """

    # ── 1. Read file ──────────────────────────────────────
    content = await file.read()
    filename = file.filename or "upload.csv"

    # ── 2. Parse for DB storage ───────────────────────────
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    raw_rows = bom_service.parse_csv_content(text)
    if not raw_rows:
        raise HTTPException(status_code=400, detail="No data rows found in file")

    vendor_service.seed_vendors(db)

    bom = bom_service.create_bom(
        db, raw_rows,
        file_name=filename,
        file_type=filename.split(".")[-1],
        user_id=user.id if user else None,
    )

    # ── 3. Call BOM Analyzer service via HTTP ─────────────
    try:
        analyzer_output = await analyzer_service.call_analyzer(
            file_bytes=content,
            filename=filename,
            user_location=delivery_location,
            target_currency=target_currency,
        )
    except RuntimeError as e:
        # Analyzer failed — mark BOM as uploaded (not analyzed) and report error
        logger.error(f"Analyzer call failed for BOM {bom.id}: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    # ── 4. Enrich + Strategy ──────────────────────────────
    parts = bom_service.get_bom_parts_as_dicts(db, bom.id)
    external_pricing = pricing_service.fetch_external_pricing(parts)
    enriched = pricing_service.enrich_analysis_with_pricing(
        analyzer_output, db, external_pricing
    )

    if priority not in ("cost", "speed"):
        priority = "cost"

    vendor_memories = vendor_service.get_vendor_memories(db)
    strategy = build_strategy_output(
        analyzer_output, delivery_location, vendor_memories,
        pricing_history=[],
        external_pricing=external_pricing,
        db=db,
        priority=priority,
    )
    procurement = generate_procurement_plan(
        strategy, target_currency, max_suppliers=5
    )

    # ── 5. Store EVERYTHING in DB ─────────────────────────
    ps = strategy.get("procurement_strategy", {})
    cs = ps.get("cost_summary", {})
    rec = strategy.get("recommended_strategy", {})
    cost_range = cs.get("range", [0, 0])

    analysis = AnalysisResult(
        bom_id=bom.id,
        user_id=user.id if user else None,
        raw_analyzer_output=analyzer_output,
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

    # ── 6. Return based on auth state ─────────────────────
    if user:
        return BOMUploadResponse(
            bom_id=bom.id,
            session_token="",
            total_parts=bom.total_parts,
            status=bom.status,
            preview=_build_full_response(
                analyzer_output, strategy, procurement, bom, priority
            ),
        )
    else:
        return BOMUploadResponse(
            bom_id=bom.id,
            session_token=bom.session_token,
            total_parts=bom.total_parts,
            status=bom.status,
            preview=_build_preview_response(
                analyzer_output, strategy, bom, priority
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

    # Auth check: either owner or valid session token
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
# Response builders — preview vs full
# ═══════════════════════════════════════════════════════════

def _build_preview_response(analyzer, strategy, bom, priority):
    """
    Guest response: limited data only.
    Shows enough to demonstrate value, hides detailed breakdown.
    """
    s1 = analyzer.get("section_1_executive_summary", {})
    ps = strategy.get("procurement_strategy", {})
    rec = strategy.get("recommended_strategy", {})

    return {
        "is_preview": True,
        # Shown to guest
        "cost_range": s1.get("cost_range", {}),
        "total_cost": s1.get("total_cost"),
        "lead_time": s1.get("lead_time", {}),
        "risk_level": ps.get("risk_analysis", {}).get("risk_level", "MEDIUM"),
        "total_parts": bom.total_parts,
        "priority": priority,
        "basic_processes": (rec.get("reasons", []))[:3],
        "region_distribution": strategy.get("region_distribution", {}),
        "decision_summary": strategy.get("decision_summary", "")[:200],
        # CTA
        "unlock_message": "Sign up to see full BOM breakdown, cost optimization, and procurement plan",
    }


def _build_full_response(analyzer, strategy, procurement, bom, priority):
    """
    Authenticated response: everything.
    analyzer_report contains the same section_1 through section_6
    structure that the frontend report renderer already expects.
    """
    return {
        "is_preview": False,
        "analyzer_report": analyzer,
        "strategy": strategy,
        "procurement_plan": procurement,
        "total_parts": bom.total_parts,
        "priority": priority,
    }