
"""
Idempotency middleware.

For configured mutating endpoints, checks the ``Idempotency-Key`` header
against a Redis store. On duplicate key within TTL, returns the cached
response without re-executing the handler.

References: GAP-015, architecture.md CC-03, api-contract-review ARP-07
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# TTL for idempotency records (seconds)
_TTL_TRANSACTIONAL = 7 * 24 * 3600   # 7 days
_TTL_AUDIT = 30 * 24 * 3600          # 30 days (audit trail)

# Routes that REQUIRE an Idempotency-Key on POST/PUT/PATCH.
# Patterns use a simplified regex syntax (path params as {…}).
IDEMPOTENT_ROUTE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/api/v1/projects/[^/]+/rfqs/[^/]+/award$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders/[^/]+/goods-receipt$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders/[^/]+/invoices$"),
    re.compile(r"^/api/v1/vendor/rfqs/[^/]+/quotes$"),
    re.compile(r"^/api/v1/chat/threads/[^/]+/messages$"),
]

# Routes that ACCEPT but do not require an Idempotency-Key
IDEMPOTENT_OPTIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/api/v1/projects$"),
    re.compile(r"^/api/v1/projects/[^/]+/rfqs$"),
]


def _matches_route(path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.match(path) for p in patterns)


def _is_required_route(path: str) -> bool:
    return _matches_route(path, IDEMPOTENT_ROUTE_PATTERNS)


def _is_idempotent_route(path: str) -> bool:
    return (
        _matches_route(path, IDEMPOTENT_ROUTE_PATTERNS)
        or _matches_route(path, IDEMPOTENT_OPTIONAL_PATTERNS)
    )


def _cache_key(idempotency_key: str, user_id: str | None) -> str:
    """Scope the key to the user to prevent cross-user collisions."""
    raw = f"{user_id or 'anon'}:{idempotency_key}"
    return f"idem:{hashlib.sha256(raw.encode()).hexdigest()}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that intercepts mutating requests on configured routes,
    checks Redis for a previously stored response under the same
    ``Idempotency-Key``, and returns the cached response on hit.

    Falls back to a no-op (pass-through) when Redis is unavailable.
    """

    def __init__(self, app: Any, redis_client: Any = None) -> None:
        super().__init__(app)
        self._redis = redis_client

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        path = request.url.path
        idem_key = request.headers.get("Idempotency-Key", "").strip()

        # If the route requires a key and none was provided → 400
        if not idem_key and _is_required_route(path):
            return JSONResponse(
                status_code=400,
                content={
                    "error_code": "IDEMPOTENCY_KEY_REQUIRED",
                    "message": "Idempotency-Key header is required for this endpoint",
                },
            )

        # If no key supplied or route not configured, pass through
        if not idem_key or not _is_idempotent_route(path):
            return await call_next(request)

        # Redis unavailable → degrade gracefully
        if self._redis is None:
            logger.debug("Redis unavailable — skipping idempotency check")
            return await call_next(request)

        user_id = getattr(request.state, "user_id", None)
        cache_key = _cache_key(idem_key, user_id)

        # Check for cached response
        try:
            cached = await self._redis.get(cache_key)
        except Exception:
            logger.warning("Redis GET failed for idempotency key", exc_info=True)
            cached = None

        if cached is not None:
            try:
                data = json.loads(cached)
                logger.info("Idempotency cache hit for key=%s", idem_key)
                return JSONResponse(
                    status_code=data.get("status_code", 200),
                    content=data.get("body"),
                    headers={"X-Idempotency-Replay": "true"},
                )
            except (json.JSONDecodeError, TypeError):
                logger.warning("Corrupt idempotency cache entry — executing fresh")

        # Execute the request
        response = await call_next(request)

        # Cache the response if it was successful (2xx)
        if 200 <= response.status_code < 300:
            try:
                body_bytes = b""
                async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                    if isinstance(chunk, str):
                        body_bytes += chunk.encode("utf-8")
                    else:
                        body_bytes += chunk

                cache_entry = json.dumps({
                    "status_code": response.status_code,
                    "body": json.loads(body_bytes) if body_bytes else None,
                })
                await self._redis.setex(cache_key, _TTL_TRANSACTIONAL, cache_entry)

                # Return a new response since we consumed the body iterator
                return JSONResponse(
                    status_code=response.status_code,
                    content=json.loads(body_bytes) if body_bytes else None,
                    headers=dict(response.headers),
                )
            except Exception:
                logger.warning("Failed to cache idempotency response", exc_info=True)

        return response
