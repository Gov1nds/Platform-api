"""
Idempotency middleware — Blueprint §32.3, C34.

Uses redis_getter callable for lazy Redis initialization.
"""
from __future__ import annotations
import json, logging, re
from typing import Any, Callable
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)
CACHE_SECONDS = 86400

IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH"}

IDEMPOTENT_ROUTE_PATTERNS = [
    re.compile(r"^/api/v1/projects/[^/]+/rfqs/[^/]+/award$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders/[^/]+/goods-receipt$"),
    re.compile(r"^/api/v1/projects/[^/]+/purchase-orders/[^/]+/invoices$"),
    re.compile(r"^/api/v1/vendor/rfqs/[^/]+/quotes$"),
    re.compile(r"^/api/v1/chat/threads/[^/]+/messages$"),
    re.compile(r"^/api/v1/rfq/create$"),
    re.compile(r"^/api/v1/orders/po$"),
]
IDEMPOTENT_OPTIONAL_PATTERNS = [
    re.compile(r"^/api/v1/projects$"),
    re.compile(r"^/api/v1/projects/[^/]+/rfqs$"),
]

def _matches(path, patterns):
    return any(p.match(path) for p in patterns)

def _is_required(path):
    return _matches(path, IDEMPOTENT_ROUTE_PATTERNS)

def _is_idempotent(path):
    return _matches(path, IDEMPOTENT_ROUTE_PATTERNS) or _matches(path, IDEMPOTENT_OPTIONAL_PATTERNS)

class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, redis_getter: Callable[[], Any] | None = None, **kw):
        super().__init__(app)
        self._redis_getter = redis_getter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in IDEMPOTENT_METHODS:
            return await call_next(request)
        path = request.url.path
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key and _is_required(path):
            return JSONResponse(status_code=400, content={
                "error_code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key header is required for this endpoint"})
        if not key or not _is_idempotent(path):
            return await call_next(request)
        redis = self._redis_getter() if self._redis_getter else None
        if redis is None:
            return await call_next(request)
        cache_key = f"idem:{request.method}:{path}:{key}"
        try:
            cached = await redis.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                data = json.loads(cached)
                return Response(content=data["body"], status_code=data["status"],
                    headers={**data.get("headers", {}), "X-Idempotent-Replay": "true"})
            except Exception:
                pass
        response = await call_next(request)
        if 200 <= response.status_code < 300:
            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk if isinstance(chunk, bytes) else chunk.encode()
                await redis.setex(cache_key, CACHE_SECONDS, json.dumps({
                    "status": response.status_code,
                    "body": body.decode("utf-8", errors="ignore"),
                    "headers": {k: v for k, v in response.headers.items() if k.lower() in ("content-type",)},
                }))
                return Response(content=body, status_code=response.status_code, headers=dict(response.headers))
            except Exception:
                logger.warning("Failed to cache idempotency response", exc_info=True)
        return response
