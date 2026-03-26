"""
PGI Manufacturing Intelligence Platform — FastAPI Application

Run: uvicorn app.main:app --reload
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db, SessionLocal
from app.routes import auth, bom, analysis, rfq, tracking, projects

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


# -------------------------------------------------------------------
# App Initialization
# -------------------------------------------------------------------
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Manufacturing Intelligence Platform — BOM Analysis, Procurement Strategy, RFQ Execution",
)


# -------------------------------------------------------------------
# Middleware (CORS)
# -------------------------------------------------------------------
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


# -------------------------------------------------------------------
# Startup Event
# -------------------------------------------------------------------
@app.on_event("startup")
def startup():
    logger.info("Initializing database...")
    init_db()

    # Lazy import to avoid circular imports
    from app.services import vendor_service

    db = SessionLocal()
    try:
        logger.info("Seeding vendors...")
        vendor_service.seed_vendors(db)
    finally:
        db.close()

    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(bom.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(rfq.router, prefix=settings.API_PREFIX)
app.include_router(tracking.router, prefix=settings.API_PREFIX)
app.include_router(projects.router, prefix=settings.API_PREFIX)


# -------------------------------------------------------------------
# Health Endpoints
# -------------------------------------------------------------------
@app.get("/", tags=["System"])
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational",
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}