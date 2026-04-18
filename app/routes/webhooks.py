from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.aftership_client import AfterShipClient
from app.services.logistics.tracking_service import tracking_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/aftership")
async def aftership_webhook(
    request: Request,
    db: Session = Depends(get_db),
    aftership_hmac_sha256: str | None = Header(default=None),
):
    raw_body = await request.body()
    client = AfterShipClient()
    if not client.verify_signature(raw_body=raw_body, header_signature=aftership_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid AfterShip signature")
    payload = await request.json()
    try:
        result = tracking_service.ingest_aftership_webhook(db, payload=payload)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", **result}


# ── Payment webhook (stub for Stripe/Razorpay) ──────────────────────────────

@router.post("/payment/{provider}")
async def payment_webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Stub for future payment gateway webhook (Stripe/Razorpay)."""
    payload = await request.json()
    from app.services.event_service import track
    track(
        db, event_type="payment_webhook_received",
        metadata={"provider": provider, "event_type": payload.get("type", "unknown")},
    )
    db.commit()
    return {"status": "ok", "provider": provider}


# ── Vendor portal RFQ response via API ───────────────────────────────────────

@router.post("/vendor-portal/rfq-response")
async def vendor_rfq_response_webhook(
    request: Request,
    portal_token: str = "",
    db: Session = Depends(get_db),
):
    """
    Allow external vendor systems to submit a quote via API.
    Validates portal_token against RFQVendorInvitation.
    """
    if not portal_token:
        raise HTTPException(400, "Missing portal_token")

    from app.models.rfq import RFQVendorInvitation
    invitation = db.query(RFQVendorInvitation).filter(
        RFQVendorInvitation.portal_token == portal_token,
    ).first()
    if not invitation:
        raise HTTPException(403, "Invalid or expired portal token")

    payload = await request.json()
    from app.services.event_service import track
    track(
        db, event_type="vendor_rfq_response_api",
        resource_type="rfq_invitation", resource_id=str(invitation.id),
        metadata={"payload_keys": list(payload.keys())},
    )
    db.commit()
    return {"status": "received", "invitation_id": str(invitation.id)}


@router.post("/dhl")
async def dhl_webhook(request: Request, db: Session = Depends(get_db)):
    import hmac, hashlib, json
    body = await request.body()
    sig = request.headers.get("dhl-signature", "")
    from app.core.config import settings
    expected = hmac.new(settings.DHL_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid DHL signature")
    payload = json.loads(body)
    return {"status": "ok"}


@router.post("/fedex")
async def fedex_webhook(request: Request, db: Session = Depends(get_db)):
    import json
    body = await request.body()
    payload = json.loads(body)
    return {"status": "ok"}


@router.post("/ups")
async def ups_webhook(request: Request, db: Session = Depends(get_db)):
    import json
    body = await request.body()
    payload = json.loads(body)
    return {"status": "ok"}
