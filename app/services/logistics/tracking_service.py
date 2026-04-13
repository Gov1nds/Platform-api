from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.enums import ShipmentStatus
from app.integrations.aftership_client import AfterShipClient
from app.models.logistics import Shipment, ShipmentMilestone
from app.services.integration_logging import integration_run

logger = logging.getLogger(__name__)


STATUS_MAP = {
    "pending": ShipmentStatus.BOOKED,
    "info_received": ShipmentStatus.BOOKED,
    "in_transit": ShipmentStatus.IN_TRANSIT,
    "out_for_delivery": ShipmentStatus.OUT_FOR_DELIVERY,
    "attempt_fail": ShipmentStatus.DELIVERY_FAILED,
    "exception": ShipmentStatus.CUSTOMS_HOLD,
    "expired": ShipmentStatus.DELIVERY_FAILED,
    "delivered": ShipmentStatus.DELIVERED,
    "available_for_pickup": ShipmentStatus.OUT_FOR_DELIVERY,
}

CHECKPOINT_TO_MILESTONE = {
    "info_received": "booked",
    "pending": "booked",
    "intransit": "in_transit",
    "pickup": "picked_up",
    "delivered": "delivered",
    "outfordelivery": "out_for_delivery",
    "exception": "exception",
    "failedattempt": "delivery_failed",
}


class TrackingService:
    def __init__(self) -> None:
        self.client = AfterShipClient()

    def register_tracking(self, db: Session, *, shipment: Shipment) -> dict:
        if not shipment.tracking_number:
            raise RuntimeError("Shipment tracking_number is missing")
        slug = shipment.carrier.lower().strip().replace(" ", "-") if shipment.carrier else None
        with integration_run(db, integration_id="INT-005", provider="aftership", operation="create_tracking", payload={"shipment_id": shipment.id, "tracking_number": shipment.tracking_number}) as run:
            resp = self.client.create_tracking(
                tracking_number=shipment.tracking_number,
                slug=slug,
                title=f"shipment:{shipment.id}",
                order_id=shipment.po_id,
            )
            shipment.carrier_integration_id = ((resp.get("data") or {}).get("tracking") or {}).get("id")
            shipment.shipment_metadata = {**(shipment.shipment_metadata or {}), "aftership": resp}
            run["response_count"] = 1
            return resp

    def ingest_aftership_webhook(self, db: Session, *, payload: dict) -> dict:
        data = payload.get("data") or {}
        tracking = data.get("tracking") or payload.get("tracking") or {}
        tracking_number = tracking.get("tracking_number")
        tracking_id = tracking.get("id") or tracking.get("legacy_id")
        if not tracking_number and not tracking_id:
            raise RuntimeError("AfterShip payload missing tracking identity")

        shipment = db.query(Shipment).filter(
            (Shipment.tracking_number == tracking_number) |
            (Shipment.carrier_integration_id == tracking_id)
        ).first()
        if not shipment:
            raise RuntimeError("No shipment matched AfterShip webhook")

        checkpoints = tracking.get("checkpoints") or []
        inserted = 0
        for cp in checkpoints:
            event_id = cp.get("checkpoint_time") or cp.get("slug") or cp.get("message")
            exists = db.query(ShipmentMilestone).filter(
                ShipmentMilestone.shipment_id == shipment.id,
                ShipmentMilestone.carrier_event_id == event_id,
            ).first()
            if exists:
                continue
            tag = (cp.get("tag") or cp.get("subtag") or "update").lower().replace("_", "")
            milestone_type = CHECKPOINT_TO_MILESTONE.get(tag, tag or "update")
            occurred_at = self._parse_dt(cp.get("checkpoint_time")) or datetime.now(timezone.utc)
            milestone = ShipmentMilestone(
                shipment_id=shipment.id,
                organization_id=shipment.organization_id,
                milestone_type=milestone_type,
                status="completed",
                location=cp.get("location"),
                notes=cp.get("message") or cp.get("subtag_message"),
                source="aftership_webhook",
                carrier_event_id=event_id,
                is_delay=tag in {"exception", "failedattempt"},
                occurred_at=occurred_at,
            )
            db.add(milestone)
            inserted += 1

        tag = (tracking.get("tag") or "").lower()
        mapped_status = STATUS_MAP.get(tag)
        if mapped_status:
            shipment.status = mapped_status
        shipment.carrier = tracking.get("slug") or shipment.carrier
        shipment.last_event_at = self._parse_dt(tracking.get("updated_at")) or datetime.now(timezone.utc)
        shipment.eta = self._parse_dt(tracking.get("expected_delivery_date") or tracking.get("estimated_delivery_date")) or shipment.eta
        if shipment.status == ShipmentStatus.DELIVERED:
            shipment.actual_delivery = self._parse_dt(tracking.get("shipment_delivery_date")) or shipment.actual_delivery or datetime.now(timezone.utc)
        shipment.shipment_metadata = {**(shipment.shipment_metadata or {}), "aftership_last_payload": payload}
        return {"shipment_id": shipment.id, "inserted_milestones": inserted, "status": shipment.status}

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None


tracking_service = TrackingService()
