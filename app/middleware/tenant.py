"""
Tenant isolation middleware.

Extracts organization_id and vendor_id from JWT claims and injects
them into ``request.state`` for downstream query scoping.

References: GAP-005, NFR-005, architecture.md CC-01
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import decode_access_token

logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return None


class TenantIsolationMiddleware(BaseHTTPMiddleware):
    """
    Populate ``request.state`` with tenant context from the JWT:

    - ``organization_id``  — used for row-level scoping on all queries
    - ``vendor_id``        — present when the caller is a vendor user
    - ``user_id``          — subject claim
    - ``role``             — caller's role
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = _extract_bearer(request)
        if token:
            payload = decode_access_token(token)
            if payload:
                request.state.organization_id = payload.organization_id
                request.state.vendor_id = payload.vendor_id
                request.state.user_id = payload.sub
                request.state.role = payload.role
            else:
                self._set_empty(request)
        else:
            self._set_empty(request)

        response = await call_next(request)
        return response

    @staticmethod
    def _set_empty(request: Request) -> None:
        request.state.organization_id = None
        request.state.vendor_id = None
        request.state.user_id = None
        request.state.role = None