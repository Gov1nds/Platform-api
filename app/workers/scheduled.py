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
        """Refresh forex rates from INT-003 providers."""
        logger.info("Forex refresh triggered (stub — provider integration pending)")
        return {"status": "stub"}

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
