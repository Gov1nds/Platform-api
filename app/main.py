"""
PGI Manufacturing Intelligence Platform — FastAPI Application

Run: uvicorn app.main:app --reload
"""
import logging
from app.schemas import project
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.routes import auth, bom, analysis, rfq, tracking  # add projects

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Manufacturing Intelligence Platform — BOM Analysis, Procurement Strategy, RFQ Execution",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    logging.getLogger("main").info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")


# ── Routes ──
from app.routes import auth, bom, analysis, rfq, tracking

app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(bom.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(rfq.router, prefix=settings.API_PREFIX)
app.include_router(tracking.router, prefix=settings.API_PREFIX)


@app.get("/")
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational",
        "docs": "/docs",
        "endpoints": {
            "auth": f"{settings.API_PREFIX}/auth",
            "bom": f"{settings.API_PREFIX}/bom",
            "analysis": f"{settings.API_PREFIX}/analysis",
            "rfq": f"{settings.API_PREFIX}/rfq",
            "tracking": f"{settings.API_PREFIX}/tracking",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}
