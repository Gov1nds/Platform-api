"""Database engine, session factory, and Base — PostgreSQL on Railway."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# -------------------------------------------------------------------
# Engine Configuration
# -------------------------------------------------------------------
connect_args = {}

if settings.is_sqlite:
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)


# -------------------------------------------------------------------
# SQLite Optimizations (dev only)
# -------------------------------------------------------------------
if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# -------------------------------------------------------------------
# Session & Base
# -------------------------------------------------------------------
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# -------------------------------------------------------------------
# Dependency (FastAPI)
# -------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------------------------------------------------
# Initialize Database
# -------------------------------------------------------------------
def init_db():
    """
    For PostgreSQL: tables are created by the bootstrap SQL migration.
    This only needs to import models so SQLAlchemy knows about them
    for ORM queries. For SQLite dev: creates tables via metadata.
    """
    # Import models to register them with SQLAlchemy
    import app.models.user
    import app.models.project
    import app.models.bom
    import app.models.analysis
    import app.models.vendor
    import app.models.pricing
    import app.models.rfq
    import app.models.tracking
    import app.models.memory
    import app.models.drawing

    # Only auto-create tables for SQLite (local dev)
    if settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
