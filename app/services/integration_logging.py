from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.models.market import IntegrationRunLog


@contextmanager
def integration_run(db: Session, *, integration_id: str, provider: str, operation: str, payload: dict | None = None):
    start = time.perf_counter()
    error_message = None
    response_count = None
    status = "success"
    try:
        state = {"response_count": None, "status": "success"}
        yield state
        response_count = state.get("response_count")
        status = state.get("status") or "success"
    except Exception as exc:
        status = "failed"
        error_message = str(exc)[:500]
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        request_payload_hash = None
        if payload is not None:
            request_payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        db.add(
            IntegrationRunLog(
                integration_id=integration_id,
                provider=provider,
                operation=operation,
                status=status,
                latency_ms=latency_ms,
                error_message=error_message,
                request_payload_hash=request_payload_hash,
                response_record_count=response_count,
            )
        )
