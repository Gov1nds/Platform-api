"""Workflow coordination helpers — idempotency, audit, and async post-upload jobs."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.workflow_command import WorkflowCommand

logger = logging.getLogger("workflow_service")


def payload_fingerprint(payload: Any) -> str:
    """Stable fingerprint for idempotency checks."""
    encoded = json.dumps(
        payload or {},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def begin_command(
    db: Session,
    *,
    namespace: str,
    idempotency_key: Optional[str],
    payload: Any,
    request_method: str,
    request_path: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    related_id: Optional[str] = None,
) -> Tuple[Optional[WorkflowCommand], Optional[Dict[str, Any]]]:
    """
    Reserve an idempotent command slot.

    Returns:
      (command, cached_response)
    """
    if not idempotency_key:
        return None, None

    payload_hash = payload_fingerprint(payload)
    existing = (
        db.query(WorkflowCommand)
        .filter(
            WorkflowCommand.namespace == namespace,
            WorkflowCommand.idempotency_key == idempotency_key,
        )
        .first()
    )

    if existing:
        if existing.payload_hash != payload_hash:
            raise ValueError(
                f"Idempotency key reuse detected for '{namespace}' with different payload."
            )
        if existing.response_json:
            return existing, existing.response_json
        if existing.status == "processing":
            raise ValueError(f"Command '{namespace}' is already processing for this key.")
        return existing, None

    command = WorkflowCommand(
        namespace=namespace,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        request_method=request_method,
        request_path=request_path,
        user_id=user_id,
        project_id=project_id,
        related_id=related_id,
        status="processing",
        response_json={},
    )
    db.add(command)
    db.flush()
    return command, None


def complete_command(
    db: Session,
    command: Optional[WorkflowCommand],
    response_json: Dict[str, Any],
) -> None:
    if not command:
        return
    command.status = "completed"
    command.response_json = response_json or {}
    command.error_text = None
    db.flush()


def fail_command(
    db: Session,
    command: Optional[WorkflowCommand],
    error_text: str,
) -> None:
    if not command:
        return
    command.status = "failed"
    command.error_text = error_text
    db.flush()


def audit_project_event(
    db: Session,
    project,
    event_type: str,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    actor_user_id: Optional[str] = None,
):
    """
    Unified audit wrapper. Reuses the existing ProjectEvent table.
    """
    from app.services import project_service

    return project_service.record_project_event(
        db,
        project,
        event_type,
        old_status=old_status,
        new_status=new_status,
        payload=payload or {},
        actor_user_id=actor_user_id,
    )


def can_access_project(user, project) -> bool:
    if not user or not project:
        return False
    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        return True
    return bool(project.user_id and project.user_id == user.id)


def schedule_post_upload_finalize(bom_id: str, project_id: str):
    """
    Async background job for the heavy non-critical resolver/review work.
    This keeps the upload response fast while preserving the full workflow.
    """
    db = SessionLocal()
    try:
        from app.models.bom import BOM
        from app.services import bom_service, resolver_service, review_service, project_service

        bom = db.query(BOM).filter(BOM.id == bom_id).first()
        project = project_service.get_project_by_id(db, project_id)

        if not bom or not project:
            return

        parts = bom_service.get_bom_parts_as_dicts(db, bom.id)
        if not parts:
            return

        source_file = bom.source_file_name or bom.file_name or "upload.csv"

        logger.info("[workflow] background resolver start | bom=%s project=%s", bom.id, project.id)

        match_results = resolver_service.resolve_and_learn(
            db,
            parts,
            bom.id,
            source_file=source_file,
        )

        if match_results:
            resolver_service.update_bom_parts_with_matches(
                db,
                bom.id,
                match_results,
                parts,
            )
            review_service.create_review_items_from_resolver(
                db, bom.id, match_results, parts
            )

        db.commit()
        logger.info("[workflow] background resolver done | bom=%s project=%s", bom.id, project.id)

    except Exception as exc:
        db.rollback()
        logger.warning("[workflow] background resolver failed: %s", exc, exc_info=True)
    finally:
        db.close()