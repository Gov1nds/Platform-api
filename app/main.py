"""
PGI Manufacturing Intelligence Platform — FastAPI Application
PostgreSQL on Railway edition.

Run: uvicorn app.main:app --reload
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db, SessionLocal
from app.routes import auth, bom, analysis, rfq, tracking, projects, drawings

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


@app.on_event("startup")
def startup():
    logger.info("Initializing database...")
    init_db()

    # Run supplementary migration for tracking tables
    if settings.is_postgres:
        try:
            from migrations.m002_add_tracking_tables import run as run_m002
            logger.info("Running supplementary migration (tracking tables)...")
            run_m002()
        except Exception as e:
            logger.warning(f"Supplementary migration skipped: {e}")

    from app.services import vendor_service
    db = SessionLocal()
    try:
        logger.info("Seeding vendors...")
        vendor_service.seed_vendors(db)
    finally:
        db.close()

    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")


app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(bom.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(rfq.router, prefix=settings.API_PREFIX)
app.include_router(tracking.router, prefix=settings.API_PREFIX)
app.include_router(projects.router, prefix=settings.API_PREFIX)
app.include_router(drawings.router, prefix=settings.API_PREFIX)


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
