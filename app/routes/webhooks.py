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
