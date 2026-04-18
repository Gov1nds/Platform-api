"""
RFQ dispatch service — sends RFQ invitations to vendors via email or portal.

Supports email (SendGrid), portal link generation, and batch dispatch.
Includes reminder functionality for non-responsive vendors.

References: Blueprint Section 12
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Result of a single RFQ dispatch attempt."""
    success: bool
    channel: str = "email"
    message_id: str | None = None
    error: str | None = None
    vendor_id: str | None = None


class RFQDispatchService:
    """Dispatch RFQ invitations to vendors via email or portal."""

    def _render_email(self, context: dict[str, Any]) -> tuple[str, str]:
        """Render HTML and plain-text email from Jinja2 templates."""
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            import os
            template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
            env = Environment(
                loader=FileSystemLoader(template_dir),
                autoescape=select_autoescape(["html"]),
            )
            html_template = env.get_template("rfq_email.html")
            html_body = html_template.render(**context)
            # Plain text fallback
            text_body = (
                f"RFQ #{context.get('rfq_id', 'N/A')} from {context.get('buyer_org_name', 'PGI Hub')}\n\n"
                f"Dear {context.get('vendor_name', 'Vendor')},\n\n"
                f"You have been invited to submit a quote for {context.get('items_count', 0)} items.\n"
                f"Deadline: {context.get('deadline', 'N/A')}\n\n"
                f"Submit your quote: {context.get('portal_link', '')}\n\n"
                f"Contact: {context.get('buyer_contact', '')}\n"
            )
            return html_body, text_body
        except Exception:
            logger.exception("Template rendering failed, using plain text fallback")
            text_body = (
                f"RFQ #{context.get('rfq_id', 'N/A')}\n"
                f"Items: {context.get('items_count', 0)}\n"
                f"Deadline: {context.get('deadline', 'N/A')}\n"
                f"Portal: {context.get('portal_link', '')}\n"
            )
            return text_body, text_body

    def _send_email(self, to_email: str, subject: str, html_body: str, text_body: str) -> str | None:
        """Send email via SendGrid. Returns message_id or None."""
        try:
            from app.integrations.sendgrid_client import send_email
            result = send_email(
                to_email=to_email,
                subject=subject,
                html_content=html_body,
                text_content=text_body,
            )
            return result.get("message_id") if isinstance(result, dict) else str(result)
        except Exception:
            logger.warning("SendGrid not configured or send failed for %s", to_email)
            return None

    def dispatch(self, db: Session, rfq: Any, invitation: Any) -> DispatchResult:
        """Dispatch a single RFQ invitation to a vendor."""
        try:
            vendor = db.query(_vendor_model()).filter_by(id=invitation.vendor_id).first()
            if not vendor:
                return DispatchResult(success=False, error="Vendor not found", vendor_id=str(invitation.vendor_id))

            vendor_email = getattr(vendor, "contact_email", None) or ""
            vendor_name = getattr(vendor, "name", "Vendor")

            # Generate portal access token
            portal_token = str(uuid.uuid4())
            invitation.portal_token = portal_token
            invitation.status = "sent"
            invitation.sent_at = datetime.now(timezone.utc)

            portal_link = f"{settings.ALLOWED_ORIGINS[0] if settings.ALLOWED_ORIGINS else 'https://app.pgihub.com'}/vendor-portal/rfq/{rfq.id}?token={portal_token}"

            context = {
                "rfq_id": str(rfq.id),
                "deadline": str(getattr(rfq, "deadline", "7 days")),
                "vendor_name": vendor_name,
                "buyer_org_name": "PGI Hub",
                "items_count": len(getattr(rfq, "items", [])),
                "portal_link": portal_link,
                "buyer_contact": "",
            }

            subject = f"RFQ #{str(rfq.id)[:8]} — Quote Request from PGI Hub"
            html_body, text_body = self._render_email(context)

            message_id = None
            if vendor_email:
                message_id = self._send_email(vendor_email, subject, html_body, text_body)

            if message_id:
                invitation.dispatch_message_id = message_id

            db.add(invitation)
            db.commit()

            _track_event(db, "rfq_dispatched", rfq_id=str(rfq.id), vendor_id=str(invitation.vendor_id))

            return DispatchResult(
                success=True,
                channel="email" if vendor_email else "portal",
                message_id=message_id,
                vendor_id=str(invitation.vendor_id),
            )
        except Exception as e:
            logger.exception("RFQ dispatch failed for invitation %s", getattr(invitation, "id", "?"))
            db.rollback()
            return DispatchResult(success=False, error=str(e), vendor_id=str(getattr(invitation, "vendor_id", "")))

    def send_reminder(self, db: Session, rfq: Any, invitation: Any) -> DispatchResult:
        """Send a reminder email for a pending RFQ invitation."""
        if getattr(invitation, "status", "") in ("responded", "quoted", "declined"):
            return DispatchResult(success=False, error="Already responded")

        vendor = db.query(_vendor_model()).filter_by(id=invitation.vendor_id).first()
        vendor_email = getattr(vendor, "contact_email", "") if vendor else ""
        if not vendor_email:
            return DispatchResult(success=False, error="No vendor email")

        subject = f"Reminder: RFQ #{str(rfq.id)[:8]} — Deadline Approaching"
        text_body = (
            f"This is a reminder that your quote for RFQ #{str(rfq.id)[:8]} "
            f"is due by {getattr(rfq, 'deadline', 'soon')}.\n"
            f"Please submit via the vendor portal."
        )
        message_id = self._send_email(vendor_email, subject, text_body, text_body)
        return DispatchResult(success=bool(message_id), channel="email", message_id=message_id)

    def dispatch_batch(self, db: Session, rfq: Any) -> list[DispatchResult]:
        """Dispatch to all pending invitations in an RFQ."""
        from app.models.rfq import RFQVendorInvitation
        invitations = db.query(RFQVendorInvitation).filter_by(
            rfq_batch_id=rfq.id,
        ).all()
        results = []
        for inv in invitations:
            if getattr(inv, "status", "draft") in ("draft", "pending"):
                results.append(self.dispatch(db, rfq, inv))
        return results


def _vendor_model():
    from app.models.vendor import Vendor
    return Vendor


def _track_event(db: Session, event_type: str, **kwargs: Any) -> None:
    try:
        from app.services.event_service import track
        track(db, event_type=event_type, metadata=kwargs)
    except Exception:
        pass


rfq_dispatch_service = RFQDispatchService()


# ── Task 23: Minimum 3 vendors per line + dispatch channels (Blueprint §12.1) ──

MIN_VENDORS_PER_LINE = 3

def validate_rfq_vendor_coverage(db, rfq_batch_id: str):
    """Ensure every RFQ line has at least 3 invited vendors."""
    from sqlalchemy import text
    from fastapi import HTTPException
    rows = db.execute(text("""
        SELECT ri.rfq_item_id, COUNT(DISTINCT rvi.vendor_id) AS n_vendors
        FROM rfq_items ri
        LEFT JOIN rfq_vendor_invitations rvi ON rvi.rfq_batch_id = ri.rfq_batch_id
        WHERE ri.rfq_batch_id = :rid
        GROUP BY ri.rfq_item_id
    """), {"rid": rfq_batch_id}).fetchall()
    short = [str(r.rfq_item_id) for r in rows if r.n_vendors < MIN_VENDORS_PER_LINE]
    if short:
        raise HTTPException(400,
            f"Minimum {MIN_VENDORS_PER_LINE} vendors per line required. "
            f"Lines short: {short}")

async def dispatch_rfq_to_vendor(db, rfq, invitation):
    """Send RFQ to vendor via best available channel."""
    from datetime import datetime, timezone
    vendor = invitation.vendor if hasattr(invitation, "vendor") else None
    ch = "portal"  # default
    if vendor and hasattr(vendor, "preferred_channel"):
        ch = getattr(vendor, "preferred_channel", "portal")
    if ch == "email":
        try:
            from app.integrations.sendgrid_client import send_email
            await send_email(to=vendor.email, subject=f"RFQ from PGI Hub",
                html=f"<p>You have a new RFQ. Please log in to respond.</p>")
        except Exception:
            pass
    invitation.sent_at = datetime.now(timezone.utc)
    if hasattr(invitation, "dispatch_channel"):
        invitation.dispatch_channel = ch
