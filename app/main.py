"""
PGI Manufacturing Intelligence Platform — FastAPI Application
PostgreSQL on Railway edition.

Run: uvicorn app.main:app --reload
"""
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db, SessionLocal, check_db_connection
from app.routes import auth, bom, analysis, rfq, tracking, projects, drawings, review, chat, approvals, vendors, analytics, reports, integrations
from app.routes import intake
from app.services import analyzer_service
from app.services.storage_service import validate_storage_configuration
from app.services.integration_service import maybe_record_api_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Manufacturing Intelligence Platform — BOM Analysis, Procurement Strategy, RFQ Execution",
)
app.include_router(intake.router, prefix=settings.API_PREFIX)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.pgihub.com",
        "https://pgihub.com",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _validate_runtime_dependencies():
    check_db_connection()
    validate_storage_configuration(strict=False)

    if settings.is_production or settings.ANALYZER_READINESS_REQUIRED:
        analyzer_status = analyzer_service.health_check_sync()
        if analyzer_status.get("status") != "ok":
            raise RuntimeError(f"Analyzer service is unreachable: {analyzer_status.get('error', analyzer_status)}")


def _run_runtime_bootstrap():
    """
    Runtime bootstrap remains available for dev/staging,
    but is disabled by default in production.
    """
    if not settings.ENABLE_RUNTIME_BOOTSTRAP:
        logger.info("Runtime bootstrap disabled by configuration.")
        return

    db = SessionLocal()
    try:
        if settings.is_postgres and settings.ENABLE_RUNTIME_MIGRATIONS:
            try:
                from alembic.config import Config as AlembicConfig
                from alembic import command as alembic_cmd

                alembic_cfg = AlembicConfig("alembic.ini")
                alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
                alembic_cmd.stamp(alembic_cfg, "head")
                logger.info("Alembic migration state stamped")
            except Exception as e:
                logger.warning(f"Alembic stamp skipped: {e}")

            try:
                from migrations.m003_indexes_and_backfill import run as run_m003
                logger.info("Running migration 003 (indexes, catalog tables, backfill)...")
                run_m003()
            except Exception as e:
                logger.warning(f"Migration 003 skipped: {e}")

            try:
                from migrations.m002_add_tracking_tables import run as run_m002
                logger.info("Running migration 002 (tracking tables)...")
                run_m002()
            except Exception as e:
                logger.warning(f"Migration 002 skipped: {e}")

        if settings.ENABLE_RUNTIME_SEEDS:
            try:
                from app.services import vendor_service
                logger.info("Seeding vendors...")
                vendor_service.seed_vendors(db)
            except Exception as e:
                logger.warning(f"Vendor seeding skipped: {e}")

            try:
                from app.services.geo_service import seed_geo_data
                logger.info("Seeding geo data...")
                seed_geo_data(db)
            except Exception as e:
                logger.warning(f"Geo seeding skipped: {e}")

            try:
                from app.services.seed_service import seed_canonical_parts
                logger.info("Seeding canonical parts...")
                seed_canonical_parts(db)
            except Exception as e:
                logger.warning(f"Canonical part seeding skipped: {e}")

        if settings.ENABLE_RUNTIME_PRICE_EXPIRY:
            try:
                from app.services.pricing_service import expire_stale_prices
                expired = expire_stale_prices(db)
                if expired:
                    logger.info(f"Expired {expired} stale pricing quotes")
            except Exception as e:
                logger.warning(f"Price expiration skipped: {e}")

        if settings.ENABLE_RUNTIME_MEMORY_DECAY:
            try:
                from app.services.memory_service import decay_old_data
                decay_old_data(db, days_threshold=180)
            except Exception as e:
                logger.warning(f"Memory decay skipped: {e}")

    finally:
        db.close()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    try:
        db = SessionLocal()
        try:
            maybe_record_api_error(
                db,
                request_method=request.method,
                request_path=str(request.url.path),
                error_text=str(exc),
                payload_json={"query_params": dict(request.query_params)},
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.on_event("startup")
def startup():
    logger.info("Initializing database models...")
    init_db(bootstrap=False)
    _validate_runtime_dependencies()
    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")


app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(bom.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(rfq.router, prefix=settings.API_PREFIX)
app.include_router(tracking.router, prefix=settings.API_PREFIX)
app.include_router(projects.router, prefix=settings.API_PREFIX)
app.include_router(drawings.router, prefix=settings.API_PREFIX)
app.include_router(review.router, prefix=settings.API_PREFIX)
app.include_router(vendors.router, prefix=settings.API_PREFIX)
app.include_router(chat.router, prefix=settings.API_PREFIX)
app.include_router(approvals.router, prefix=settings.API_PREFIX)
app.include_router(analytics.router, prefix=settings.API_PREFIX)
app.include_router(reports.router, prefix=settings.API_PREFIX)
app.include_router(integrations.router, prefix=settings.API_PREFIX)


@app.get("/", tags=["System"])
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational",
        "database": "postgresql" if settings.is_postgres else "sqlite",
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}