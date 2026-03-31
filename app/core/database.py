"""
Database engine, session factory, and Base — PostgreSQL on Railway.
"""

import os
import logging
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

logger = logging.getLogger("database")

# -------------------------------------------------------------------
# Engine Configuration
# -------------------------------------------------------------------

connect_args = {}

if settings.is_sqlite:
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,

    # Connection health
    pool_pre_ping=True,

    # Pool tuning (Railway safe)
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,  # 30 mins

    # Streaming large queries
    execution_options={"stream_results": True},

    echo=False,
)

# -------------------------------------------------------------------
# PostgreSQL Session Settings
# -------------------------------------------------------------------

if settings.is_postgres:

    @event.listens_for(engine, "connect")
    def set_postgres_session(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()

        cursor.execute("SET statement_timeout TO 30000")
        cursor.execute("SET lock_timeout TO 10000")
        cursor.execute("SET idle_in_transaction_session_timeout TO 30000")

        cursor.close()

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
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# -------------------------------------------------------------------
# Database Health Check
# -------------------------------------------------------------------

def check_db_connection():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise RuntimeError(f"Database connection failed: {e}")

# -------------------------------------------------------------------
# Initialize Database
# -------------------------------------------------------------------

def init_db():
    """
    Import all models so SQLAlchemy registers them, then ensure schemas/tables exist.
    """

    # Import models (CRITICAL for metadata registration)
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
    import app.models.catalog
    import app.models.geo
    import app.models.report_snapshot
    import app.models.strategy_run

    # -------------------------------------------------------------------
    # PostgreSQL Schema Creation
    # -------------------------------------------------------------------

    if settings.is_postgres:
        schemas = (
            "auth", "bom", "projects", "pricing",
            "sourcing", "ops", "geo", "catalog"
        )

        with engine.begin() as conn:
            for schema in schemas:
                try:
                    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                except Exception as e:
                    logger.error(f"Failed creating schema {schema}: {e}")
                    raise

    # -------------------------------------------------------------------
    # Table Creation Strategy
    # -------------------------------------------------------------------

    allow_raw = os.getenv("ALLOW_CREATE_ALL")

    if allow_raw is None:
        allow_create = not settings.is_production
    else:
        allow_create = allow_raw.lower() in ("true", "1", "yes")

    if allow_create:
        Base.metadata.create_all(bind=engine)
        logger.info("Tables created via metadata.create_all()")
    else:
        logger.info(
            "ALLOW_CREATE_ALL=false (ENVIRONMENT=%s) — skipping create_all(). "
            "Use 'alembic upgrade head' for schema changes.",
            getattr(settings, "ENVIRONMENT", "unknown"),
        )