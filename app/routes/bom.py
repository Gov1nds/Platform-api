"""
BOM routes — upload, analyze, unlock, and project snapshot creation.

Integration points (in upload order):
  1. analyzer_service   — AI extraction of BOM parts
  2. bom_service        — persist BOM + BOMPart rows
  3. pricing_service    — external price enrichment
  4. strategy_service   — procurement strategy build
  5. procurement_planner— line-item plan generation
  6. project_service    — upsert Project snapshot
  7. resolver_service   — canonical part matching + learning loop
  8. review_service     — queue unresolved / soft-matched parts for human review

Updated for PostgreSQL schema.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.bom import BOM
from app.models.analysis import AnalysisResult
from app.schemas.bom import BOMUploadResponse, BOMUnlockRequest, BOMUnlockResponse
from app.utils.dependencies import get_current_user
from app.services import (
    bom_service,
    analyzer_service,
    pricing_service,
    vendor_service,
    project_service,
)
from app.services.strategy_service import build_strategy_output
from app.services.procurement_planner import generate_procurement_plan
from app.services import resolver_service
from app.services import review_service
from app.services.workflow_service import begin_command, complete_command, fail_command

logger = logging.getLogger("routes.bom")
router = APIRouter(prefix="/bom", tags=["bom"])


# ══════════════════════════════════════════════════════════════════════════════
# UPLOAD  (main pipeline)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/upload", response_model=BOMUploadResponse)
async def bom_upload(
    file: UploadFile = File(...),
    delivery_location: str = Form("India"),
    target_currency: str = Form("USD"),
    priority: str = Form("cost"),
    user: Optional[User] = Depends(get_current_user),
    session_token: str = Form(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or "upload.csv"

    command, cached = begin_command(
        db,
        namespace="bom.upload",
        idempotency_key=idempotency_key,
        payload={
            "filename": filename,
            "delivery_location": delivery_location,
            "target_currency": target_currency,
            "priority": priority,
            "user_id": user.id if user else None,
            "session_token": session_token,
            "file_hash": hashlib.sha256(content).hexdigest(),
        },
        request_method="POST",
        request_path="/api/v1/bom/upload",
        user_id=user.id if user else None,
        related_id=filename,
    )
    if cached:
        return BOMUploadResponse.model_validate(cached)

    # ── 1. AI extraction ──────────────────────────────────────────────────────
    try:
        analyzer_output = await analyzer_service.call_analyzer(
            file_bytes=content,
            filename=filename,
            user_location=delivery_location,
            target_currency=target_currency,
        )
    except RuntimeError as e:
        logger.error("Analyzer call failed: %s", e)
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=502, detail=str(e))

    # vendor_service.seed_vendors runs at startup — no need to re-seed per request

    try:
        # ── 2. Persist BOM + BOMPart rows ─────────────────────────────────────
        bom = bom_service.create_bom_from_analyzer(
            db,
            analyzer_output,
            file_name=filename,
            file_type=filename.rsplit(".", 1)[-1] if "." in filename else "csv",
            user_id=user.id if user else None,
            session_token=session_token,
        )

        # ── 3. Build strategy input ───────────────────────────────────────────
        v2_report = analyzer_output.get("_v2_full_report")
        if v2_report and "section_2_component_breakdown" in v2_report:
            strategy_input = v2_report
        else:
            strategy_input = {
                "section_2_component_breakdown": _components_to_section_2(
                    analyzer_output.get("components", [])
                )
            }

        # ── 4. External price enrichment ─────────────────────────────────────
        parts = bom_service.get_bom_parts_as_dicts(db, bom.id)
        external_pricing = pricing_service.fetch_external_pricing(parts)
        enriched = pricing_service.enrich_analysis_with_pricing(
            strategy_input, db, external_pricing
        )

        # ── 5. Strategy + procurement plan ───────────────────────────────────
        if priority not in ("cost", "speed"):
            priority = "cost"

        vendor_memories = vendor_service.get_vendor_memories(db)
        strategy = build_strategy_output(
            strategy_input,
            delivery_location,
            vendor_memories,
            pricing_history=[],
            external_pricing=external_pricing,
            db=db,
            priority=priority,
            target_currency=target_currency,
        )
        procurement = generate_procurement_plan(
            strategy, target_currency, max_suppliers=5
        )

        # ── 6. Persist AnalysisResult ─────────────────────────────────────────
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
                    "analyzer": enriched,
                    "procurement_plan": procurement,
                    "external_pricing": {
                        k: v for k, v in external_pricing.items() if v
                    },
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

        # ── 7. Project snapshot ───────────────────────────────────────────────
        project = project_service.upsert_project_from_analysis(
            db,
            bom=bom,
            analysis=analysis,
            analyzer_output=analyzer_output,
            strategy=strategy,
            procurement=procurement,
        )

        lifecycle = project_service.persist_analysis_lifecycle(
            db,
            bom=bom,
            analysis=analysis,
            project=project,
            session_token=session_token or bom.session_token or "",
            analysis_status="guest_preview" if not user else "authenticated_unlocked",
            report_visibility_level="preview" if not user else "full",
            unlock_status="locked" if not user else "unlocked",
        )

        # ── 8. Resolver: canonical part matching + learning loop ─────────────
        match_results: list = []

        try:
            source_file = bom.source_file_name or filename

            logger.info(f"[Resolver] Starting for BOM {bom.id} with {len(parts)} parts")

            match_results = resolver_service.resolve_and_learn(
                db,
                parts,
                bom.id,
                source_file=source_file,
            )

            logger.info(
                f"[Resolver] Completed for BOM {bom.id} | "
                f"results={len(match_results)}"
            )

            resolver_service.update_bom_parts_with_matches(
                db,
                bom.id,
                match_results,
                parts,
            )

            logger.info(f"[Resolver] BOMPart linkage updated for BOM {bom.id}")

        except Exception as e:
            logger.warning(f"[Resolver] failed (non-fatal): {e}", exc_info=True)

        # ── 9. Review queue: surface unresolved / soft-match parts ───────────
        try:
            if match_results:
                review_service.create_review_items_from_resolver(
                    db, bom.id, match_results, parts
                )
        except Exception as e:
            logger.warning(f"Review queue creation failed (non-fatal): {e}")

        workspace_route = f"/project/{project.id}"
        preview_payload = (
            project_service.build_guest_preview(
                project,
                session_token=session_token or bom.session_token or "",
                analysis_status="guest_preview" if not user else "authenticated_unlocked",
                report_visibility_level="preview" if not user else "full",
                unlock_status="locked" if not user else "unlocked",
            )
            if not user
            else _build_authenticated_preview(project, analysis, strategy, procurement)
        )

        response = BOMUploadResponse(
            bom_id=bom.id,
            guest_bom_id=bom.id if not user else None,
            session_token=(session_token or bom.session_token or "") if not user else "",
            analysis_status="guest_preview" if not user else "authenticated_unlocked",
            report_visibility_level="preview" if not user else "full",
            unlock_status="locked" if not user else "unlocked",
            project_id=project.id,
            workspace_route=workspace_route,
            total_parts=bom.total_parts,
            status=bom.status,
            analysis_lifecycle=lifecycle,
            preview=preview_payload,
        )

        complete_command(db, command, response.model_dump(mode="json"))

        # ── 10. Commit + refresh ─────────────────────────────────────────────
        db.commit()
        db.refresh(bom)
        db.refresh(analysis)
        db.refresh(project)

        return response

    except Exception as e:
        try:
            fail_command(db, command, str(e))
        except Exception:
            pass
        db.rollback()
        raise


# ══════════════════════════════════════════════════════════════════════════════
# UNLOCK  (authenticated full-report access)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/unlock", response_model=BOMUnlockResponse)
def bom_unlock(
    body: BOMUnlockRequest,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bom = db.query(BOM).filter(BOM.id == body.bom_id).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")

    analysis = (
        db.query(AnalysisResult).filter(AnalysisResult.bom_id == bom.id).first()
    )
    project = project_service.get_project_by_bom_id(db, bom.id)

    authorized = False

    # Auth path 1: logged-in owner
    if user and bom.uploaded_by_user_id == user.id:
        authorized = True

    # Auth path 2: guest session token
    elif body.session_token:
        from app.models.user import GuestSession

        gs = db.query(GuestSession).filter(
            GuestSession.session_token == body.session_token,
            GuestSession.id == bom.guest_session_id,
        ).first()

        if gs:
            authorized = True

            # Opportunistically claim the BOM for a newly-logged-in user
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

    lifecycle = project_service.persist_analysis_lifecycle(
        db,
        bom=bom,
        analysis=analysis,
        project=project,
        session_token=body.session_token or bom.session_token or "",
        analysis_status="authenticated_unlocked" if user else "guest_unlocked",
        report_visibility_level="full",
        unlock_status="unlocked",
    )

    full_report = project.analyzer_report or {}
    workspace_route = lifecycle.get("workspace_route") or f"/project/{project.id}"

    return BOMUnlockResponse(
        bom_id=bom.id,
        guest_bom_id=bom.id,
        session_token=body.session_token or bom.session_token or "",
        analysis_status=lifecycle["analysis_status"],
        report_visibility_level=lifecycle["report_visibility_level"],
        unlock_status=lifecycle["unlock_status"],
        project_id=project.id,
        workspace_route=workspace_route,
        analysis_lifecycle=lifecycle,
        full_report=full_report,
        strategy=project.strategy or analysis.strategy_output or {},
        procurement_plan=project.procurement_plan or analysis.enriched_output.get("procurement_plan") or {},
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _components_to_section_2(components: list) -> list:
    """
    Normalise a flat components list into the section_2_component_breakdown
    shape expected by build_strategy_output when the v2 full report is absent.
    """
    section_2 = []
    for comp in components:
        section_2.append(
            {
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
            }
        )
    return section_2


def _build_authenticated_preview(
    project, analysis, strategy: dict, procurement: dict
) -> dict:
    """
    Full response payload for authenticated users.
    Guests receive a stripped version via project_service.build_guest_preview.
    """
    lifecycle = (project.project_metadata or {}).get("analysis_lifecycle", {}) or {}
    workspace_route = lifecycle.get("workspace_route") or f"/project/{project.id}"

    return {
        "is_preview": False,
        "guest_bom_id": lifecycle.get("guest_bom_id") or str(project.bom_id),
        "session_token": lifecycle.get("session_token") or "",
        "analysis_status": lifecycle.get("analysis_status") or "authenticated_unlocked",
        "report_visibility_level": lifecycle.get("report_visibility_level") or "full",
        "unlock_status": lifecycle.get("unlock_status") or "unlocked",
        "workspace_route": workspace_route,
        "analysis_lifecycle": {
            "guest_bom_id": lifecycle.get("guest_bom_id") or str(project.bom_id),
            "project_id": project.id,
            "session_token": lifecycle.get("session_token") or "",
            "analysis_status": lifecycle.get("analysis_status") or "authenticated_unlocked",
            "report_visibility_level": lifecycle.get("report_visibility_level") or "full",
            "unlock_status": lifecycle.get("unlock_status") or "unlocked",
            "workspace_route": workspace_route,
        },
        "project_id": project.id,
        "bom_id": project.bom_id,
        "analyzer_report": project.analyzer_report or {},
        "strategy": project.strategy or strategy,
        "procurement_plan": project.procurement_plan or procurement,
        "total_parts": project.total_parts,
        "priority": "cost",
        "currency": project.currency or strategy.get("currency", "USD"),
    }