"""
D-3 / P-7: Scheduled Jobs Worker

Runs as a separate process alongside the web server.
Handles: report generation, memory decay, price expiry, analytics rollups.

Usage:
  python -m app.scripts.run_scheduled_jobs          # run once
  python -m app.scripts.run_scheduled_jobs --loop    # run continuously (every 60s)

Railway:  Add as a worker service or cron job.
"""
import sys
import os
import time
import logging
import uuid
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import SessionLocal
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduled_jobs")


def _next_run_time(frequency: str, from_time: datetime = None) -> datetime:
    """Calculate next run time based on frequency."""
    now = from_time or datetime.utcnow()
    if frequency == "daily":
        return now + timedelta(days=1)
    elif frequency == "weekly":
        return now + timedelta(weeks=1)
    elif frequency == "monthly":
        return now + timedelta(days=30)
    elif frequency == "hourly":
        return now + timedelta(hours=1)
    else:
        return now + timedelta(weeks=1)  # default weekly


def run_due_reports(db):
    """Execute all due scheduled reports."""
    from app.models.analytics import ReportSchedule
    from app.services import analytics_service

    now = datetime.utcnow()
    due_reports = (
        db.query(ReportSchedule)
        .filter(
            ReportSchedule.is_active == True,
            ReportSchedule.next_run_at <= now,
        )
        .all()
    )

    if not due_reports:
        logger.info("No due reports found.")
        return 0

    executed = 0
    for schedule in due_reports:
        job_id = str(uuid.uuid4())[:8]
        logger.info(f"Executing report '{schedule.report_name}' (job={job_id})")

        try:
            schedule.job_correlation_id = job_id

            # Generate report data based on type
            filters = schedule.filters_json or {}
            project_id = filters.get("project_id")

            if schedule.report_type == "spend":
                report_data = analytics_service.get_spend_analytics(db, project_id=project_id)
            elif schedule.report_type == "vendor":
                report_data = analytics_service.get_vendor_analytics(db, project_id=project_id)
            elif schedule.report_type == "category":
                report_data = analytics_service.get_category_analytics(db, project_id=project_id)
            elif schedule.report_type == "trends":
                report_data = analytics_service.get_trends(db, project_id=project_id)
            elif schedule.report_type == "savings":
                report_data = analytics_service.get_savings(db, project_id=project_id)
            else:
                report_data = analytics_service.get_spend_analytics(db, project_id=project_id)

            # Send to recipients
            recipients = schedule.recipients_json or []
            if recipients:
                try:
                    from app.services.email_service import _send_email, _base_template
                    subject = f"Scheduled Report: {schedule.report_name}"
                    body = _base_template(f"""
                        <h2 style="color: white;">{schedule.report_name}</h2>
                        <p style="color: rgba(255,255,255,0.6);">
                            Your scheduled {schedule.report_type} report is ready.
                            Log in to your dashboard to view full interactive details.
                        </p>
                        <div style="margin: 16px 0;">
                            <a href="{os.getenv('FRONTEND_URL', 'https://www.pgihub.com')}/analytics"
                               style="display:inline-block;padding:12px 24px;background:#34d399;color:#050a0e;font-weight:700;border-radius:10px;text-decoration:none;">
                                View Report
                            </a>
                        </div>
                    """)
                    for email in recipients:
                        if isinstance(email, str) and "@" in email:
                            _send_email(email, subject, body)
                except Exception as email_err:
                    logger.warning(f"Report email delivery failed: {email_err}")

            # Update schedule state
            schedule.last_run_at = now
            schedule.last_run_status = "success"
            schedule.last_run_error = None
            schedule.total_runs = (schedule.total_runs or 0) + 1
            schedule.consecutive_failures = 0
            schedule.next_run_at = _next_run_time(schedule.frequency, now)
            db.flush()
            executed += 1
            logger.info(f"Report '{schedule.report_name}' executed successfully. Next run: {schedule.next_run_at}")

        except Exception as exc:
            logger.error(f"Report '{schedule.report_name}' failed: {exc}")
            schedule.last_run_at = now
            schedule.last_run_status = "error"
            schedule.last_run_error = str(exc)[:500]
            schedule.consecutive_failures = (schedule.consecutive_failures or 0) + 1
            schedule.next_run_at = _next_run_time(schedule.frequency, now)

            # Disable after 5 consecutive failures
            if schedule.consecutive_failures >= 5:
                schedule.is_active = False
                logger.warning(f"Report '{schedule.report_name}' disabled after 5 consecutive failures")

            db.flush()

    db.commit()
    logger.info(f"Executed {executed}/{len(due_reports)} due reports.")
    return executed


def run_price_expiry(db):
    """Expire stale pricing data."""
    try:
        from app.services.pricing_service import expire_stale_prices
        expired = expire_stale_prices(db)
        if expired:
            logger.info(f"Expired {expired} stale prices.")
            db.commit()
        return expired or 0
    except Exception as e:
        logger.warning(f"Price expiry skipped: {e}")
        db.rollback()
        return 0


def run_memory_decay(db):
    """Decay old supplier memory data."""
    try:
        from app.services.memory_service import decay_old_data
        decay_old_data(db, days_threshold=180)
        db.commit()
        logger.info("Memory decay completed.")
    except Exception as e:
        logger.warning(f"Memory decay skipped: {e}")
        db.rollback()


def run_analytics_rollups(db):
    """Refresh analytics rollup tables."""
    try:
        from app.services.analytics_service import refresh_project_rollups
        refresh_project_rollups(db)
        db.commit()
        logger.info("Analytics rollups refreshed.")
    except Exception as e:
        logger.warning(f"Analytics rollups skipped: {e}")
        db.rollback()


def run_all_jobs():
    """Execute all scheduled maintenance tasks."""
    logger.info("=== Starting scheduled jobs run ===")
    db = SessionLocal()
    try:
        run_due_reports(db)
        run_price_expiry(db)
        run_memory_decay(db)
        run_analytics_rollups(db)
    except Exception as e:
        logger.error(f"Scheduled jobs error: {e}")
        db.rollback()
    finally:
        db.close()
    logger.info("=== Scheduled jobs run complete ===")


def main():
    loop_mode = "--loop" in sys.argv
    interval = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60"))

    if loop_mode:
        logger.info(f"Starting scheduler loop (interval={interval}s)")
        while True:
            try:
                run_all_jobs()
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            time.sleep(interval)
    else:
        run_all_jobs()


if __name__ == "__main__":
    main()
