"""Auth service — guest session merge and user lifecycle."""
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app.models.user import User, GuestSession
from app.models.bom import BOM
from app.models.analysis import AnalysisResult
from app.models.project import Project
from app.models.rfq import RFQBatch
from app.services.project_service import record_project_event, normalize_project_stage

logger = logging.getLogger("auth_service")


def merge_guest_session(
    db: Session,
    user_id: str,
    session_token: Optional[str],
) -> Dict[str, Any]:
    """
    Transactionally merge all guest session data to an authenticated user.

    Merges: BOMs, Projects, AnalysisResults, RFQ batches.
    Updates: GuestSession.merged_user_id/merged_at.
    Returns: audit summary dict.
    """
    if not session_token:
        return {"merged": False, "reason": "no_session_token"}

    guest = (
        db.query(GuestSession)
        .filter(
            GuestSession.session_token == session_token,
            GuestSession.merged_user_id.is_(None),
        )
        .first()
    )

    if not guest:
        return {"merged": False, "reason": "session_not_found_or_already_merged"}

    try:
        # 1. Merge BOMs
        boms_updated = (
            db.query(BOM)
            .filter(
                BOM.guest_session_id == guest.id,
                BOM.uploaded_by_user_id.is_(None),
            )
            .update(
                {"uploaded_by_user_id": user_id, "updated_at": datetime.utcnow()},
                synchronize_session="fetch",
            )
        )

        # 2. Merge Projects — also advance to project_hydrated
        guest_projects = (
            db.query(Project)
            .filter(
                Project.guest_session_id == guest.id,
                Project.user_id.is_(None),
            )
            .all()
        )
        projects_updated = 0
        merged_project_ids = []
        for project in guest_projects:
            old_stage = project.workflow_stage
            project.user_id = user_id
            project.updated_at = datetime.utcnow()

            # Advance from guest_preview to project_hydrated
            if old_stage in ("draft", "guest_preview"):
                project.workflow_stage = "project_hydrated"
                project.status = "project_hydrated"
                project.visibility = "full"
                project.visibility_level = "full"

            # Update metadata
            meta = dict(project.project_metadata or {})
            meta["workflow_stage"] = project.workflow_stage
            meta["analysis_status"] = "authenticated_unlocked"
            meta["report_visibility_level"] = "full"
            meta["unlock_status"] = "unlocked"
            project.project_metadata = meta

            record_project_event(
                db,
                project,
                event_type="guest_session_merged",
                old_status=old_stage,
                new_status=project.workflow_stage,
                payload={
                    "guest_session_id": guest.id,
                    "user_id": user_id,
                },
                actor_user_id=user_id,
            )

            projects_updated += 1
            merged_project_ids.append(str(project.id))

        # 3. Merge AnalysisResults
        analyses_updated = (
            db.query(AnalysisResult)
            .filter(
                AnalysisResult.guest_session_id == guest.id,
                AnalysisResult.user_id.is_(None),
            )
            .update(
                {"user_id": user_id, "updated_at": datetime.utcnow()},
                synchronize_session="fetch",
            )
        )

        # 4. Merge RFQ batches
        rfqs_updated = (
            db.query(RFQBatch)
            .filter(
                RFQBatch.guest_session_id == guest.id,
                RFQBatch.requested_by_user_id.is_(None),
            )
            .update(
                {"requested_by_user_id": user_id, "updated_at": datetime.utcnow()},
                synchronize_session="fetch",
            )
        )

        # 5. Mark guest session as merged
        guest.merged_user_id = user_id
        guest.merged_at = datetime.utcnow()
        guest.updated_at = datetime.utcnow()

        db.flush()

        result = {
            "merged": True,
            "guest_session_id": guest.id,
            "boms_merged": boms_updated,
            "projects_merged": projects_updated,
            "project_ids": merged_project_ids,
            "analyses_merged": analyses_updated,
            "rfqs_merged": rfqs_updated,
        }

        logger.info(f"Guest merge completed: {result}")
        return result

    except Exception as e:
        logger.error(f"Guest merge failed: {e}", exc_info=True)
        db.rollback()
        return {"merged": False, "reason": str(e)}