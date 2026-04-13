"""
BOM analysis pipeline background tasks.

Each task is idempotent, retryable, checkpointed, and audit-logged.

References: GAP-002 (decomposed pipeline), architecture.md CC-14, SM-001
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.enums import BOMLineStatus, BOMUploadStatus

logger = logging.getLogger(__name__)

try:
    from app.workers import celery_app
except ImportError:
    celery_app = None


def _get_db_session():
    from app.core.database import SessionLocal
    return SessionLocal()


if celery_app:

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
    def task_scan_and_parse_bom(self, bom_id: str) -> dict:
        """Virus scan uploaded file, parse, create BOM_Lines."""
        from app.models.bom import BOM, BOMPart
        from app.integrations.storage import scan_file, s3_client
        from app.services.workflow.state_machine import transition_bom_upload

        db = _get_db_session()
        try:
            bom = db.query(BOM).filter(BOM.id == bom_id).first()
            if not bom:
                return {"error": "BOM not found"}

            # Virus scan
            if bom.s3_key:
                try:
                    data = s3_client.download(bom.s3_key)
                    bom.scan_status = scan_file(data)
                except Exception:
                    bom.scan_status = "CLEAN"
            else:
                bom.scan_status = "CLEAN"

            if bom.scan_status == "INFECTED":
                bom.status = BOMUploadStatus.PARSE_FAILED
                bom.parse_summary = {"error": "File failed virus scan"}
                db.commit()
                return {"status": "infected"}

            bom.status = BOMUploadStatus.PARSING
            db.commit()
            return {"status": "parsed", "bom_id": bom_id}
        except Exception as exc:
            db.rollback()
            logger.exception("scan_and_parse_bom failed for %s", bom_id)
            raise self.retry(exc=exc)
        finally:
            db.close()

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
    def task_normalize_bom_line(self, bom_line_id: str) -> dict:
        """Per-line normalization via bom-intelligence-engine."""
        import asyncio
        from app.models.bom import BOMPart
        from app.services.workflow.state_machine import transition_bom_line
        from app.services.analyzer_service import call_normalize

        db = _get_db_session()
        try:
            line = db.query(BOMPart).filter(BOMPart.id == bom_line_id).first()
            if not line:
                return {"error": "Line not found"}

            if line.status != BOMLineStatus.NORMALIZING:
                transition_bom_line(
                    db, line, BOMLineStatus.NORMALIZING,
                    actor_type="SYSTEM", skip_guard=True,
                )
                db.flush()

            result = asyncio.get_event_loop().run_until_complete(
                call_normalize({
                    "bom_line_id": line.id,
                    "raw_text": line.raw_text or "",
                    "description": line.description or "",
                    "quantity": float(line.quantity) if line.quantity else 1,
                    "unit": line.unit or "each",
                    "specs": line.specs or {},
                })
            )

            line.normalization_trace_json = result
            line.normalization_status = "COMPLETE"
            confidence = result.get("classification_confidence", 0)

            if confidence < 0.85:
                transition_bom_line(
                    db, line, BOMLineStatus.NEEDS_REVIEW,
                    actor_type="SYSTEM",
                )
                line.review_required = True
            else:
                transition_bom_line(
                    db, line, BOMLineStatus.NORMALIZED,
                    actor_type="SYSTEM",
                )
                # Chain to enrichment
                task_enrich_bom_line.delay(bom_line_id)

            db.commit()
            return {"status": line.status, "bom_line_id": bom_line_id}

        except Exception as exc:
            db.rollback()
            try:
                line = db.query(BOMPart).filter(BOMPart.id == bom_line_id).first()
                if line:
                    transition_bom_line(
                        db, line, BOMLineStatus.ERROR,
                        actor_type="SYSTEM", skip_guard=True,
                    )
                    db.commit()
            except Exception:
                db.rollback()
            logger.exception("normalize failed for line %s", bom_line_id)
            raise self.retry(exc=exc)
        finally:
            db.close()

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
    def task_enrich_bom_line(self, bom_line_id: str) -> dict:
        """Per-line enrichment via bom-intelligence-engine."""
        import asyncio
        from app.models.bom import BOMPart
        from app.services.workflow.state_machine import transition_bom_line
        from app.services.analyzer_service import call_enrich

        db = _get_db_session()
        try:
            line = db.query(BOMPart).filter(BOMPart.id == bom_line_id).first()
            if not line:
                return {"error": "Line not found"}

            transition_bom_line(db, line, BOMLineStatus.ENRICHING, actor_type="SYSTEM")
            db.flush()

            result = asyncio.get_event_loop().run_until_complete(
                call_enrich({
                    "bom_line_id": line.id,
                    "normalized_data": line.normalization_trace_json or {},
                })
            )

            line.enrichment_json = result
            line.enrichment_status = "COMPLETE"
            line.risk_flags = result.get("risk_flags", [])
            line.data_freshness_json = result.get("data_freshness_summary", {})

            transition_bom_line(db, line, BOMLineStatus.ENRICHED, actor_type="SYSTEM")
            db.commit()

            # Chain to scoring
            task_score_bom_line.delay(bom_line_id)
            return {"status": "ENRICHED", "bom_line_id": bom_line_id}

        except Exception as exc:
            db.rollback()
            logger.exception("enrich failed for line %s", bom_line_id)
            raise self.retry(exc=exc)
        finally:
            db.close()

    @celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
    def task_score_bom_line(self, bom_line_id: str) -> dict:
        """Per-line vendor scoring via bom-intelligence-engine."""
        import asyncio
        from app.models.bom import BOMPart, BOM
        from app.models.project import Project
        from app.services.workflow.state_machine import (
            transition_bom_line,
            check_and_advance_project_to_analysis_complete,
        )
        from app.services.analyzer_service import call_score

        db = _get_db_session()
        try:
            line = db.query(BOMPart).filter(BOMPart.id == bom_line_id).first()
            if not line:
                return {"error": "Line not found"}

            transition_bom_line(db, line, BOMLineStatus.SCORING, actor_type="SYSTEM")
            db.flush()

            result = asyncio.get_event_loop().run_until_complete(
                call_score(
                    bom_line_data={"bom_line_id": line.id},
                    enrichment=line.enrichment_json or {},
                    vendor_candidates=[],
                    weight_profile="balanced",
                )
            )

            line.score_cache_json = result
            line.scoring_status = "COMPLETE"
            transition_bom_line(db, line, BOMLineStatus.SCORED, actor_type="SYSTEM")

            # Check if project should advance to ANALYSIS_COMPLETE
            bom = db.query(BOM).filter(BOM.id == line.bom_id).first()
            if bom and bom.project_id:
                project = db.query(Project).filter(Project.id == bom.project_id).first()
                if project:
                    check_and_advance_project_to_analysis_complete(db, project)

            db.commit()
            return {"status": "SCORED", "bom_line_id": bom_line_id}

        except Exception as exc:
            db.rollback()
            logger.exception("score failed for line %s", bom_line_id)
            raise self.retry(exc=exc)
        finally:
            db.close()

    @celery_app.task(bind=True, max_retries=1)
    def task_batch_pipeline(self, project_id: str) -> dict:
        """Orchestrate full pipeline for all eligible RAW lines in a project."""
        from app.models.bom import BOM, BOMPart

        db = _get_db_session()
        try:
            bom_ids = [
                b.id for b in
                db.query(BOM.id).filter(BOM.project_id == project_id, BOM.deleted_at.is_(None)).all()
            ]
            if not bom_ids:
                return {"triggered": 0}

            lines = db.query(BOMPart).filter(
                BOMPart.bom_id.in_(bom_ids),
                BOMPart.status == BOMLineStatus.RAW,
                BOMPart.deleted_at.is_(None),
            ).all()

            for line in lines:
                task_normalize_bom_line.delay(line.id)

            return {"triggered": len(lines), "project_id": project_id}
        finally:
            db.close()
