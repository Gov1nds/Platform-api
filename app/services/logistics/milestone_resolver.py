"""Carrier milestone → PO state machine advancement (Blueprint §13)."""
from __future__ import annotations
import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CARRIER_MILESTONE_MAP = {
    "Pending": None,
    "InfoReceived": "PO_SENT",
    "InTransit": "IN_TRANSIT",
    "OutForDelivery": "IN_TRANSIT",
    "AttemptFail": None,
    "Delivered": "DELIVERED",
    "AvailableForPickup": "DELIVERED",
    "Exception": None,
    "Expired": None,
}

def advance_po_from_milestone(db: Session, po, tag: str):
    target = CARRIER_MILESTONE_MAP.get(tag)
    if not target:
        return
    try:
        from app.services.workflow.state_machine import can_transition_po, transition_po
        if can_transition_po(po.status, target):
            transition_po(db, po, target, actor_type="SYSTEM", notes=f"Carrier milestone: {tag}")
    except Exception:
        logger.exception("Failed to advance PO %s from milestone %s", po.id, tag)
