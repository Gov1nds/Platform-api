"""Per-state SLA enforcement (Blueprint §13.1, C22)."""
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.rfq import PurchaseOrder
from app.models.events import EventAuditLog
import logging

logger = logging.getLogger(__name__)

SLA_WINDOWS = {
    "PO_APPROVED": None,
    "PO_SENT": timedelta(hours=24),
    "VENDOR_ACCEPTED": timedelta(days=3),
    "PRODUCTION_STARTED": None,
    "QUALITY_CHECK": timedelta(days=2),
    "PACKED": timedelta(days=1),
    "SHIPPED": None,
    "CUSTOMS": timedelta(days=3),
    "IN_TRANSIT": None,
    "DELIVERED": timedelta(days=2),
    "GR_CONFIRMED": timedelta(days=7),
}

def check_sla_breaches(db: Session) -> int:
    now = datetime.now(timezone.utc)
    breaches = 0
    try:
        pos = db.query(PurchaseOrder).filter(
            PurchaseOrder.status.notin_(["CLOSED", "CANCELLED"])).all()
    except Exception:
        return 0

    for po in pos:
        last = db.query(EventAuditLog).filter_by(
            entity_type="purchase_order", entity_id=str(po.id)
        ).order_by(EventAuditLog.created_at.desc()).first()
        if not last: continue
        last_at = last.created_at
        if last_at and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)

        window = SLA_WINDOWS.get(po.status)
        breached = False
        reason = None

        if window is not None:
            if (now - last_at) > window:
                breached = True
                reason = f"{po.status} exceeded {window}"
        elif po.status == "PRODUCTION_STARTED":
            edd = getattr(po, "expected_delivery_date", None)
            if edd and edd < now:
                breached = True
                reason = f"Past production lead time ({edd})"
        elif po.status == "IN_TRANSIT":
            edd = getattr(po, "expected_delivery_date", None)
            if edd and edd < now:
                breached = True
                reason = f"Past ETA ({edd})"
        elif po.status == "SHIPPED":
            lst = getattr(po, "last_state_transition_at", None)
            if lst and (now - lst) > timedelta(hours=12):
                breached = True
                reason = "No carrier update in 12h"

        if breached:
            try:
                from app.services.notification_service import notification_service
                notification_service.send(db, user_id=getattr(po, "buyer_user_id", None) or str(po.vendor_id),
                    event_type="PO_DELAYED",
                    context_data={"po_id": str(po.id), "status": po.status, "reason": reason},
                    channels=("email", "in-app"))
            except Exception:
                logger.debug("Notification failed for SLA breach", exc_info=True)
            breaches += 1
    return breaches
