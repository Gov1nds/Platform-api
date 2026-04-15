from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.integrations.connector_wrapper import connector_call_guard
from app.integrations.http import build_sync_client


class OpenExchangeRatesClient:
    base_url = "https://openexchangerates.org/api"

    def __init__(self, app_id: str | None = None) -> None:
        self.app_id = app_id or settings.OPEN_EXCHANGE_RATES_APP_ID

    def configured(self) -> bool:
        return bool(self.app_id)

    def latest(self, *, symbols: list[str] | None = None, base: str | None = None) -> dict:
        if not self.configured():
            raise RuntimeError("OpenExchangeRates is not configured")

        params: dict[str, str] = {"app_id": self.app_id}
        if symbols:
            params["symbols"] = ",".join(sorted(set(symbols)))
        if base and base.upper() != "USD":
            params["base"] = base.upper()

        def _request() -> dict[str, Any]:
            with build_sync_client(base_url=self.base_url) as client:
                resp = client.get("/latest.json", params=params)
                resp.raise_for_status()
                return resp.json()

        payload = connector_call_guard.execute(
            connector_name="open_exchange_rates",
            operation="latest",
            func=_request,
            cache_key_payload=params,
            fallback_factory=None,
            priority="active",
            max_requests_per_minute=60,
            max_retries=2,
        )
        payload["rates"] = {k: Decimal(str(v)) for k, v in payload.get("rates", {}).items()}
        return payload