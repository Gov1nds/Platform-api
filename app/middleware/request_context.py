"""
Request context middleware.

Generates or propagates ``X-Request-ID`` and W3C ``traceparent`` headers
for distributed tracing. Binds IDs into structlog context variables.

References: GAP-015, architecture.md CC-11, NFR-002
"""
from __future__ import annotations

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

try:
    import structlog
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False


def _extract_trace_id(request: Request) -> str | None:
    """Extract trace-id from W3C traceparent header if present."""
    traceparent = request.headers.get("traceparent")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 2:
            return parts[1]
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Inject ``request_id`` and ``trace_id`` into request state and
    response headers for end-to-end correlation.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        trace_id = _extract_trace_id(request) or request_id

        request.state.request_id = request_id
        request.state.trace_id = trace_id

        try:
            from app.services.geoip_service import geoip_service
            geo = geoip_service.resolve_request(request)
            request.state.geoip = geo
        except Exception:
            request.state.geoip = None

        if _HAS_STRUCTLOG:
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(
                request_id=request_id,
                trace_id=trace_id,
            )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response