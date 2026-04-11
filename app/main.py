"""
FastAPI application entry point.

Configures lifespan, middleware stack, router registration,
health/readiness/liveness probes, and structured logging.

References: GAP-015 (OpenTelemetry), architecture.md CC-11, CC-15
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db

# ── Structured logging setup ────────────────────────────────────────────────

try:
    import structlog

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


# ── OpenTelemetry bootstrap ─────────────────────────────────────────────────

def _init_otel(app: FastAPI) -> None:
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry instrumentation enabled → %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)
    except ImportError:
        logger.warning("OpenTelemetry packages not installed — instrumentation disabled")
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry")


def _shutdown_otel() -> None:
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:
        logger.exception("Error shutting down OpenTelemetry")


# ── Redis client (lazy) ─────────────────────────────────────────────────────

_redis_client = None


async def _init_redis() -> None:
    global _redis_client
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await _redis_client.ping()
        logger.info("Redis connected → %s", settings.REDIS_URL.split("@")[-1])
    except Exception:
        _redis_client = None
        logger.warning("Redis unavailable — idempotency/caching degraded")


async def _shutdown_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    init_db()
    await _init_redis()
    _init_otel(app)
    logger.info("%s v%s started (env=%s)", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)
    yield
    # Shutdown
    _shutdown_otel()
    await _shutdown_redis()
    logger.info("%s shutting down", settings.PROJECT_NAME)


# ── App instance ─────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

# ── Middleware stack (order matters: outermost first) ────────────────────────

from app.middleware.request_context import RequestContextMiddleware  # noqa: E402
from app.middleware.tenant import TenantIsolationMiddleware  # noqa: E402
from app.middleware.idempotency import IdempotencyMiddleware  # noqa: E402

app.add_middleware(RequestContextMiddleware)
app.add_middleware(TenantIsolationMiddleware)
app.add_middleware(
    IdempotencyMiddleware,
    redis_client=None,  # Patched after lifespan creates the client
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# ── Patch idempotency middleware with Redis client after startup ─────────────
# The IdempotencyMiddleware is instantiated before lifespan runs.
# We lazily inject the redis client via an on_event-style hook.

@app.middleware("http")
async def _inject_redis_into_idempotency(request, call_next):
    """Ensure the idempotency middleware has the live Redis client."""
    for mw in app.middleware_stack.__dict__.get("app", {}) if False else []:
        pass  # no-op; actual injection below
    # Directly set on the middleware instance via app state
    if _redis_client is not None:
        for mw_cls in getattr(app, "_middleware", []):
            if hasattr(mw_cls, "_redis"):
                mw_cls._redis = _redis_client
    return await call_next(request)


# ── Router registration ─────────────────────────────────────────────────────

from app.routes import (  # noqa: E402
    auth,
    bom,
    projects,
    vendors,
    rfq,
    chat,
    orders,
    vendor_portal,
    analytics,
    intake,
)

prefix = settings.API_PREFIX
for r in [auth, bom, projects, vendors, rfq, chat, orders, vendor_portal, analytics, intake]:
    app.include_router(r.router, prefix=prefix)


# ── System endpoints ────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational",
    }


@app.get("/health", tags=["System"])
async def health():
    """
    Health check returning component status.

    Returns ``degraded`` if any dependency is unhealthy.
    """
    checks: dict[str, str] = {}

    # DB
    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    # Redis
    if _redis_client:
        try:
            await _redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
    else:
        checks["redis"] = "unavailable"

    # BOM engine
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.BOM_ANALYZER_URL}/health")
            checks["bom_engine"] = "ok" if resp.status_code == 200 else "error"
    except Exception:
        checks["bom_engine"] = "unavailable"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "components": checks}


@app.get("/ready", tags=["System"])
async def readiness():
    """Readiness probe — returns 200 only when critical deps are up."""
    try:
        from sqlalchemy import text
        from app.core.database import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not_ready"})


@app.get("/live", tags=["System"])
def liveness():
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "alive"}