from __future__ import annotations

from geoip2.database import Reader
from geoip2.webservice import Client as WebServiceClient

from app.core.config import settings


class MaxMindClient:
    def __init__(self) -> None:
        self.account_id = settings.MAXMIND_ACCOUNT_ID
        self.license_key = settings.MAXMIND_LICENSE_KEY
        self.db_path = settings.MAXMIND_DB_PATH
        self._reader = None
        self._ws_client = None

    def configured(self) -> bool:
        return bool((self.account_id and self.license_key) or self.db_path)

    def lookup(self, ip_address: str) -> dict | None:
        if not self.configured() or not ip_address:
            return None
        try:
            if self.db_path:
                if self._reader is None:
                    self._reader = Reader(self.db_path)
                resp = self._reader.city(ip_address)
            else:
                if self._ws_client is None:
                    self._ws_client = WebServiceClient(int(self.account_id), self.license_key)
                resp = self._ws_client.city(ip_address)
            return {
                "ip": ip_address,
                "country_iso": resp.country.iso_code,
                "country_name": resp.country.name,
                "city": resp.city.name,
                "subdivision": resp.subdivisions.most_specific.name if resp.subdivisions else None,
                "postal_code": getattr(resp.postal, "code", None),
                "lat": float(resp.location.latitude) if resp.location and resp.location.latitude is not None else None,
                "lng": float(resp.location.longitude) if resp.location and resp.location.longitude is not None else None,
                "timezone": getattr(resp.location, "time_zone", None),
            }
        except Exception:
            return None
