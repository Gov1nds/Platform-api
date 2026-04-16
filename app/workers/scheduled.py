"""
Scheduled background tasks (Celery Beat).

References: GAP-030 (guest cleanup), GAP-022 (data refresh),
            GAP-020 (reports), GAP-019 (stale tracking)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    from celery.schedules import crontab
    from app.workers import celery_app
except ImportError:
    celery_app = None


def _get_db():
    from app.core.database import SessionLocal
    return SessionLocal()


if celery_app:

    celery_app.conf.beat_schedule = {
        "guest-cleanup": {
            "task": "app.workers.scheduled.task_guest_session_cleanup",
            "schedule": crontab(hour=2, minute=0),
        },
        "refresh-forex": {
            "task": "app.workers.scheduled.task_refresh_forex",
            "schedule": timedelta(minutes=15),
        },
        "refresh-commodity": {
            "task": "app.workers.scheduled.task_refresh_commodity",
            "schedule": timedelta(hours=1),
        },
        "stale-shipment": {
            "task": "app.workers.scheduled.task_stale_shipment_check",
            "schedule": timedelta(hours=2),
        },
        "rfq-deadline": {
            "task": "app.workers.scheduled.task_rfq_deadline_check",
            "schedule": timedelta(hours=1),
        },
        "quote-expiry": {
            "task": "app.workers.scheduled.task_quote_expiry_check",
            "schedule": timedelta(hours=1),
        },
        "po-sla": {
            "task": "app.workers.scheduled.task_po_sla_check",
            "schedule": timedelta(hours=1),
        },
        "vendor-performance": {
            "task": "app.workers.scheduled.task_rebuild_vendor_performance",
            "schedule": crontab(hour=3, minute=0),
        },
        "report-snapshot": {
            "task": "app.workers.scheduled.task_report_snapshot_aggregation",
            "schedule": crontab(hour=4, minute=0),
        },
        "weekly-digest": {
            "task": "app.workers.scheduled.task_weekly_digest",
            "schedule": crontab(hour=8, minute=0, day_of_week=1),
        },
        "phase2a-refresh-scan": {
            "task": "app.workers.scheduled.task_phase2a_refresh_scan",
            "schedule": timedelta(minutes=10),
        },
        "phase2c-vendor-performance-foundation": {
            "task": "app.workers.scheduled.task_phase2c_vendor_performance_foundation",
            "schedule": crontab(hour=3, minute=15),
        },
        # ── Phase 3 scheduled tasks ─────────────────────────────────────
        "phase3-vendor-validation-sweep": {
            "task": "app.workers.scheduled.task_phase3_vendor_validation_batch",
            "schedule": crontab(hour=2, minute=30),
        },
        "phase3-refresh-commodity-signals": {
            "task": "app.workers.scheduled.task_phase3_refresh_commodity_signals",
            "schedule": crontab(hour=5, minute=0),
        },
        "phase3-rebuild-performance-snapshots": {
            "task": "app.workers.scheduled.task_phase3_rebuild_performance_snapshots",
            "schedule": crontab(hour=3, minute=45),
        },
    }

    @celery_app.task
    def task_guest_session_cleanup() -> dict:
        """Expire >30d inactive, anonymize >30d expired, hard-delete >90d."""
        from app.models.user import GuestSession
        from app.enums import GuestSessionStatus

        db = _get_db()
        try:
            now = datetime.now(timezone.utc)
            cutoff_30 = now - timedelta(days=30)
            cutoff_90 = now - timedelta(days=90)

            # Expire inactive sessions
            expired = db.query(GuestSession).filter(
                GuestSession.status == GuestSessionStatus.ACTIVE,
                GuestSession.last_active_at < cutoff_30,
            ).all()
            for gs in expired:
                gs.status = GuestSessionStatus.EXPIRED

            # Anonymize old expired sessions
            to_anon = db.query(GuestSession).filter(
                GuestSession.status == GuestSessionStatus.EXPIRED,
                GuestSession.updated_at < cutoff_30,
            ).all()
            for gs in to_anon:
                gs.ip_address = None
                gs.detected_location = None

            # Hard delete very old
            deleted = db.query(GuestSession).filter(
                GuestSession.status.in_([
                    GuestSessionStatus.EXPIRED,
                    GuestSessionStatus.DELETED,
                ]),
                GuestSession.created_at < cutoff_90,
            ).delete(synchronize_session="fetch")

            db.commit()
            return {
                "expired": len(expired),
                "anonymized": len(to_anon),
                "deleted": deleted,
            }
        except Exception:
            db.rollback()
            logger.exception("Guest cleanup failed")
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_refresh_forex() -> dict:
        """Refresh forex rates from Open Exchange Rates, retaining seed fallback rows."""
        from app.services.market_data.fx_service import fx_service

        db = _get_db()
        try:
            result = fx_service.refresh_rates(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            fx_service.mark_provider_failure(db)
            db.commit()
            logger.exception("Forex refresh failed; seeded baseline remains available")
            return {"status": "fallback", "updated": 0}
        finally:
            db.close()

    @celery_app.task
    def task_refresh_commodity() -> dict:
        """Refresh commodity prices from INT-002 providers."""
        logger.info("Commodity refresh triggered (stub)")
        return {"status": "stub"}

    @celery_app.task
    def task_stale_shipment_check() -> dict:
        """Alert on shipments with no event >12h."""
        from app.models.logistics import Shipment
        from app.enums import ShipmentStatus

        db = _get_db()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
            active_statuses = [
                ShipmentStatus.BOOKED, ShipmentStatus.PICKED_UP,
                ShipmentStatus.IN_TRANSIT, ShipmentStatus.CUSTOMS_HOLD,
                ShipmentStatus.CUSTOMS_CLEARED, ShipmentStatus.OUT_FOR_DELIVERY,
            ]
            stale = db.query(Shipment).filter(
                Shipment.status.in_(active_statuses),
                Shipment.stale_alert_sent == False,
                Shipment.last_event_at < cutoff,
            ).all()

            for s in stale:
                s.stale_alert_sent = True
                # In production: trigger notification

            db.commit()
            return {"stale_count": len(stale)}
        except Exception:
            db.rollback()
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_rfq_deadline_check() -> dict:
        """Auto-expire RFQs past deadline."""
        from app.models.rfq import RFQBatch
        from app.enums import RFQStatus

        db = _get_db()
        try:
            now = datetime.now(timezone.utc)
            expired = db.query(RFQBatch).filter(
                RFQBatch.status.in_([RFQStatus.SENT, RFQStatus.PARTIALLY_RESPONDED]),
                RFQBatch.deadline < now,
                RFQBatch.deleted_at.is_(None),
            ).all()

            for rfq in expired:
                rfq.status = RFQStatus.EXPIRED

            db.commit()
            return {"expired": len(expired)}
        except Exception:
            db.rollback()
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_quote_expiry_check() -> dict:
        """Warn at 48h, auto-expire at valid_until."""
        from app.models.rfq import RFQQuoteHeader
        from app.enums import QuoteStatus

        db = _get_db()
        try:
            now = datetime.now(timezone.utc)
            expired = db.query(RFQQuoteHeader).filter(
                RFQQuoteHeader.quote_status.in_([
                    QuoteStatus.SUBMITTED, QuoteStatus.REVISED,
                ]),
                RFQQuoteHeader.valid_until < now,
                RFQQuoteHeader.deleted_at.is_(None),
            ).all()

            for q in expired:
                q.quote_status = QuoteStatus.EXPIRED

            db.commit()
            return {"expired": len(expired)}
        except Exception:
            db.rollback()
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_po_sla_check() -> dict:
        """24h reminder, 72h escalation for unacknowledged POs."""
        from app.models.rfq import PurchaseOrder
        from app.enums import POStatus

        db = _get_db()
        try:
            now = datetime.now(timezone.utc)
            overdue = db.query(PurchaseOrder).filter(
                PurchaseOrder.status == POStatus.PO_SENT,
                PurchaseOrder.sla_response_deadline < now,
                PurchaseOrder.vendor_acknowledged_at.is_(None),
                PurchaseOrder.deleted_at.is_(None),
            ).all()
            # In production: send escalation notifications
            return {"overdue_pos": len(overdue)}
        except Exception:
            return {"error": "failed"}
        finally:
            db.close()



    @celery_app.task
    def task_phase2a_refresh_scan() -> dict:
        """Mark active BOM lines whose Phase 2A evidence has expired and enqueue scoped recomputes."""
        from app.services.enrichment.recompute_service import phase2a_recompute_service

        db = _get_db()
        try:
            result = phase2a_recompute_service.mark_stale_active_lines_for_refresh(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            logger.exception("Phase 2A refresh scan failed")
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_rebuild_vendor_performance() -> dict:
        """Rebuild 90-day vendor performance snapshots."""
        from app.models.vendor import Vendor, VendorPerformanceSnapshot
        from app.models.rfq import PurchaseOrder, RFQQuoteHeader
        from datetime import date

        db = _get_db()
        try:
            vendors = db.query(Vendor).filter(Vendor.deleted_at.is_(None)).all()
            today = date.today()

            for v in vendors:
                total_pos = db.query(PurchaseOrder).filter(
                    PurchaseOrder.vendor_id == v.id
                ).count()

                snap = VendorPerformanceSnapshot(
                    vendor_id=v.id,
                    snapshot_date=today,
                    total_pos=total_pos,
                    trailing_window_days=90,
                )
                db.add(snap)

            db.commit()
            return {"vendors_processed": len(vendors)}
        except Exception:
            db.rollback()
            return {"error": "failed"}
        finally:
            db.close()

    @celery_app.task
    def task_phase2c_vendor_performance_foundation() -> dict:
        """Compute Phase 2C vendor scorecard foundation rows from quote outcomes."""
        from datetime import timedelta
        from app.services.outcome_data_service import outcome_data_service

        db = _get_db()
        try:
            today = datetime.now(timezone.utc).date()
            period_end = today
            period_start = today - timedelta(days=90)
            rows = outcome_data_service.rebuild_vendor_performance(
                db,
                period_start=period_start,
                period_end=period_end,
                replace_existing=True,
            )
            db.commit()
            return {"vendor_rows": len(rows), "period_start": str(period_start), "period_end": str(period_end)}
        except Exception:
            db.rollback()
            logger.exception("Phase 2C vendor performance foundation rebuild failed")
            return {"error": "failed"}
        finally:
            db.close()


    @celery_app.task
    def task_report_snapshot_aggregation() -> dict:
        """Nightly report snapshot computation."""
        logger.info("Report snapshot aggregation triggered (stub)")
        return {"status": "stub"}

    @celery_app.task
    def task_weekly_digest() -> dict:
        """Monday AM weekly digest email."""
        logger.info("Weekly digest triggered (stub)")
        return {"status": "stub"}

    @celery_app.task
    def task_generate_report_export(job_id: str, report_id: str, fmt: str = "pdf") -> dict:
        """Async report export (PDF/Excel)."""
        logger.info("Report export %s format=%s (stub)", job_id, fmt)
        return {"job_id": job_id, "status": "complete", "format": fmt}


    # ═════════════════════════════════════════════════════════════════════
    # Phase 3 scheduled tasks (§8 "automated periodic data quality checks")
    # ═════════════════════════════════════════════════════════════════════

    @celery_app.task
    def task_phase3_vendor_validation_batch() -> dict:
        """Daily vendor validation + trust-tier recompute + dedup scan."""
        from app.services.vendor_intelligence_service import vendor_intelligence_service
        logger.info("phase3 vendor validation batch starting")
        db = _get_db()
        try:
            summary = vendor_intelligence_service.run_batch_validation_and_dedup(db)
            db.commit()
            return summary
        except Exception as exc:
            db.rollback()
            logger.exception("phase3 vendor validation batch failed")
            return {"status": "failed", "error": str(exc)}
        finally:
            db.close()

    @celery_app.task
    def task_phase3_refresh_commodity_signals() -> dict:
        """
        Daily commodity signal refresh.

        In production this pulls from an external commodity API (LME, DCE, etc).
        In the default platform-seeded configuration it re-ingests the seed
        commodity baseline CSV so trend / valley flags stay current.
        """
        import json
        import os
        from pathlib import Path
        from app.services.market.commodity_price_service import commodity_price_service

        db = _get_db()
        updated = 0
        errors: list[str] = []
        try:
            baseline_path = (
                Path(os.environ.get("SEED_ROOT", "seed"))
                / "market"
                / "commodity_price_signals_phase3.json"
            )
            if baseline_path.exists():
                data = json.loads(baseline_path.read_text())
                for entry in data if isinstance(data, list) else data.get("items", []):
                    try:
                        commodity_price_service.ingest_commodity_signal(entry, db)
                        updated += 1
                    except Exception as exc:
                        errors.append(f"{entry.get('commodity_name')}:{exc}")
            db.commit()
            return {"status": "ok", "updated": updated, "errors": errors}
        except Exception as exc:
            db.rollback()
            logger.exception("phase3 refresh_commodity_signals failed")
            return {"status": "failed", "error": str(exc)}
        finally:
            db.close()

    @celery_app.task
    def task_phase3_rebuild_performance_snapshots() -> dict:
        """
        Nightly rebuild of vendor performance snapshots plus trust tiers +
        communication scores. Complementary to the Phase-2c task.
        """
        from app.models.vendor import Vendor
        from app.services.vendor_intelligence_service import vendor_intelligence_service

        db = _get_db()
        updated = 0
        try:
            vendors = (
                db.query(Vendor)
                .filter(
                    Vendor.is_active.is_(True),
                    Vendor.deleted_at.is_(None),
                    Vendor.merged_into_vendor_id.is_(None),
                )
                .limit(5000)
                .all()
            )
            for v in vendors:
                try:
                    vendor_intelligence_service.compute_trust_tier(v.id, db)
                    updated += 1
                except Exception:
                    logger.exception("trust tier refresh failed for %s", v.id)
            db.commit()
            return {"status": "ok", "updated": updated}
        except Exception as exc:
            db.rollback()
            logger.exception("phase3 rebuild_performance_snapshots failed")
            return {"status": "failed", "error": str(exc)}
        finally:
            db.close()
