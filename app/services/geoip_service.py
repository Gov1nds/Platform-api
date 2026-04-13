from __future__ import annotations

import json
import logging

import redis
from starlette.requests import Request

from app.core.config import settings
from app.integrations.maxmind_client import MaxMindClient

logger = logging.getLogger(__name__)


class GeoIPService:
    CACHE_PREFIX = "geoip:"
    TTL_SECONDS = 86400

    def __init__(self) -> None:
        self.client = MaxMindClient()
        self._redis = None

    def _redis_client(self):
        if self._redis is not None:
            return self._redis
        try:
            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    def resolve_request(self, request: Request) -> dict | None:
        ip = self._extract_ip(request)
        if not ip or ip in {"127.0.0.1", "::1", "testclient"}:
            return None
        redis_client = self._redis_client()
        key = f"{self.CACHE_PREFIX}{ip}"
        if redis_client:
            cached = redis_client.get(key)
            if cached:
                return json.loads(cached)
        result = self.client.lookup(ip)
        if result and redis_client:
            redis_client.setex(key, self.TTL_SECONDS, json.dumps(result))
        return result

    @staticmethod
    def _extract_ip(request: Request) -> str | None:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return None


geoip_service = GeoIPService()
