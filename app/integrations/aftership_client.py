from __future__ import annotations

import base64
import hashlib
import hmac

from app.core.config import settings
from app.integrations.http import build_sync_client


class AfterShipClient:
    base_url = "https://api.aftership.com/tracking/2026-01"

    def __init__(self) -> None:
        self.api_key = settings.AFTERSHIP_API_KEY
        self.webhook_secret = settings.AFTERSHIP_WEBHOOK_SECRET

    def configured(self) -> bool:
        return bool(self.api_key)

    def verify_signature(self, *, raw_body: bytes, header_signature: str | None) -> bool:
        if not self.webhook_secret:
            return False
        if not header_signature:
            return False
        digest = hmac.new(
            self.webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, header_signature.strip())

    def create_tracking(self, *, tracking_number: str, slug: str | None = None, title: str | None = None, order_id: str | None = None) -> dict:
        if not self.configured():
            raise RuntimeError("AfterShip is not configured")
        payload = {"tracking": {"tracking_number": tracking_number}}
        if slug:
            payload["tracking"]["slug"] = slug
        if title:
            payload["tracking"]["title"] = title
        if order_id:
            payload["tracking"]["order_id"] = order_id
        headers = {"as-api-key": self.api_key, "Content-Type": "application/json"}
        with build_sync_client(base_url=self.base_url, headers=headers) as client:
            resp = client.post("/trackings", json=payload)
            resp.raise_for_status()
            return resp.json()
