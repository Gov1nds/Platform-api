"""
Email Notification Service — sends transactional emails for RFQ/quote events.

Uses SMTP (configurable via env vars). Falls back to logging if SMTP not configured.
Dashboard remains the source of truth; email is notification only.
"""
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger("email_service")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@pgihub.com")
FROM_NAME = os.getenv("FROM_NAME", "PGI Hub")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.pgihub.com")


def _is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not _is_configured():
        logger.info(f"Email not sent (SMTP not configured): [{subject}] → {to_email}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        logger.info(f"Email sent: [{subject}] → {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def _base_template(content: str) -> str:
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; background: #0a0e14; color: #e2e8f0;">
        <div style="margin-bottom: 24px;">
            <span style="font-size: 18px; font-weight: 700; color: #34d399;">PGI Hub</span>
        </div>
        {content}
        <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.06); font-size: 11px; color: rgba(255,255,255,0.3);">
            PGI Manufacturing Intelligence Platform · <a href="{FRONTEND_URL}" style="color: #38bdf8;">pgihub.com</a>
        </div>
    </div>
    """


def notify_rfq_submitted(user_email: str, user_name: str, project_name: str,
                          project_id: str, custom_parts_count: int = 0):
    """Notify user that their RFQ has been submitted."""
    subject = f"RFQ Submitted — {project_name}"
    content = f"""
    <h2 style="font-size: 20px; color: white; margin-bottom: 8px;">Quote Request Received</h2>
    <p style="color: rgba(255,255,255,0.6); font-size: 14px; line-height: 1.6;">
        Hi {user_name or "there"},<br><br>
        Your request for quote on <strong style="color: white;">{project_name}</strong>
        ({custom_parts_count} custom parts) has been received.
        Our team will review your requirements and provide a quote within 24 hours.
    </p>
    <div style="margin: 24px 0;">
        <a href="{FRONTEND_URL}/project/{project_id}"
           style="display: inline-block; padding: 12px 24px; background: #34d399; color: #050a0e; font-weight: 700; font-size: 14px; border-radius: 10px; text-decoration: none;">
            View Project Status
        </a>
    </div>
    <p style="color: rgba(255,255,255,0.4); font-size: 12px;">
        You can track the status of your quote on your <a href="{FRONTEND_URL}/dashboard" style="color: #38bdf8;">dashboard</a>.
    </p>
    """
    _send_email(user_email, subject, _base_template(content))


def notify_quote_ready(user_email: str, user_name: str, project_name: str,
                        project_id: str, total_cost: Optional[float] = None,
                        currency: str = "USD"):
    """Notify user that their quote is ready for review."""
    subject = f"Quote Ready — {project_name}"
    cost_line = f"<strong style='color: #34d399;'>{currency} {total_cost:,.2f}</strong>" if total_cost else "available for review"
    content = f"""
    <h2 style="font-size: 20px; color: white; margin-bottom: 8px;">Your Quote is Ready</h2>
    <p style="color: rgba(255,255,255,0.6); font-size: 14px; line-height: 1.6;">
        Hi {user_name or "there"},<br><br>
        The quote for <strong style="color: white;">{project_name}</strong> is ready:
        {cost_line}
    </p>
    <div style="margin: 24px 0;">
        <a href="{FRONTEND_URL}/project/{project_id}"
           style="display: inline-block; padding: 12px 24px; background: #34d399; color: #050a0e; font-weight: 700; font-size: 14px; border-radius: 10px; text-decoration: none;">
            Review Quote
        </a>
    </div>
    <p style="color: rgba(255,255,255,0.4); font-size: 12px;">
        This quote is valid for 30 days. Review and approve from your dashboard to proceed.
    </p>
    """
    _send_email(user_email, subject, _base_template(content))


def notify_production_update(user_email: str, user_name: str, project_name: str,
                              project_id: str, stage: str, message: str = ""):
    """Notify user of a production stage change."""
    stage_labels = {
        "T0": "Order Placed", "T1": "Material Procurement",
        "T2": "Manufacturing Started", "T3": "QC / Inspection",
        "T4": "Shipped / Delivered",
    }
    stage_label = stage_labels.get(stage, stage)
    subject = f"Production Update: {stage_label} — {project_name}"
    content = f"""
    <h2 style="font-size: 20px; color: white; margin-bottom: 8px;">Production Update</h2>
    <p style="color: rgba(255,255,255,0.6); font-size: 14px; line-height: 1.6;">
        Hi {user_name or "there"},<br><br>
        <strong style="color: white;">{project_name}</strong> has moved to:
        <span style="display: inline-block; margin-top: 8px; padding: 6px 14px; background: rgba(56,189,248,0.1); border: 1px solid rgba(56,189,248,0.2); border-radius: 8px; color: #38bdf8; font-weight: 600;">
            {stage} — {stage_label}
        </span>
    </p>
    {f'<p style="color: rgba(255,255,255,0.5); font-size: 13px; margin-top: 12px;">{message}</p>' if message else ""}
    <div style="margin: 24px 0;">
        <a href="{FRONTEND_URL}/project/{project_id}"
           style="display: inline-block; padding: 12px 24px; background: #34d399; color: #050a0e; font-weight: 700; font-size: 14px; border-radius: 10px; text-decoration: none;">
            Track Progress
        </a>
    </div>
    """
    _send_email(user_email, subject, _base_template(content))


def notify_drawing_received(user_email: str, user_name: str, project_name: str,
                             project_id: str, file_name: str):
    """Notify user that their drawing upload was received."""
    subject = f"Drawing Received — {project_name}"
    content = f"""
    <h2 style="font-size: 20px; color: white; margin-bottom: 8px;">Drawing Received</h2>
    <p style="color: rgba(255,255,255,0.6); font-size: 14px; line-height: 1.6;">
        Hi {user_name or "there"},<br><br>
        Your drawing <strong style="color: white;">{file_name}</strong> for
        <strong style="color: white;">{project_name}</strong> has been received.
        Our engineering team will review it and include it in your quote.
    </p>
    <div style="margin: 24px 0;">
        <a href="{FRONTEND_URL}/project/{project_id}"
           style="display: inline-block; padding: 12px 24px; background: rgba(167,139,250,0.15); border: 1px solid rgba(167,139,250,0.25); color: #c4b5fd; font-weight: 600; font-size: 14px; border-radius: 10px; text-decoration: none;">
            View Project
        </a>
    </div>
    """
    _send_email(user_email, subject, _base_template(content))
