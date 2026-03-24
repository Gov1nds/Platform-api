"""
PGI Manufacturing Intelligence Platform — FastAPI Application

Run: uvicorn app.main:app --reload
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.routes import auth, bom, analysis, rfq, tracking, projects

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
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://platform-api-production-d66b.up.railway.app",  # safe add
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    logging.getLogger("main").info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")


app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(bom.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(rfq.router, prefix=settings.API_PREFIX)
app.include_router(tracking.router, prefix=settings.API_PREFIX)
app.include_router(projects.router, prefix=settings.API_PREFIX)


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
            "projects": f"{settings.API_PREFIX}/projects",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}
