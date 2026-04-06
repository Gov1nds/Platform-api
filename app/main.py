import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.routes import auth, bom, projects, vendors, rfq, chat, orders, vendor_portal, analytics, intake

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)
app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

prefix = settings.API_PREFIX
for r in [auth, bom, projects, vendors, rfq, chat, orders, vendor_portal, analytics, intake]:
    app.include_router(r.router, prefix=prefix)

@app.on_event("startup")
def startup():
    logger.info("Initializing database...")
    init_db()
    logger.info(f"{settings.PROJECT_NAME} v{settings.VERSION} started")

@app.get("/", tags=["System"])
def root():
    return {"service": settings.PROJECT_NAME, "version": settings.VERSION, "status": "operational"}

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}
