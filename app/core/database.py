"""
Database engine, session factory, and Base — PostgreSQL on Railway.
"""
import os
import logging
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

logger = logging.getLogger("database")

connect_args = {}
if settings.is_sqlite:
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    echo=False,
)

if settings.is_postgres:

    @event.listens_for(engine, "connect")
    def set_postgres_session(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET statement_timeout TO 30000")
        cursor.execute("SET lock_timeout TO 10000")
        cursor.execute("SET idle_in_transaction_session_timeout TO 30000")
        cursor.close()

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    """
    FastAPI DB dependency.
    No implicit commit here; routes own their transaction boundaries.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_db_connection():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise RuntimeError(f"Database connection failed: {e}")


def init_db(bootstrap: bool = False):
    """
    Import all models so SQLAlchemy registers them.

    If bootstrap=False, no schema mutation is performed. This keeps Railway
    startup deterministic and pushes migrations into explicit release jobs.
    """
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
    import app.models.vendor_match
    import app.models.collaboration
    import app.models.analytics
    import app.models.workflow_command
    import app.models.intake
    import app.models.project_access
    import app.models.integration_assets
    
    if settings.is_postgres:
        should_create_schemas = bootstrap
        if should_create_schemas:
            schemas = (
                "auth", "bom", "projects", "pricing",
                "sourcing", "ops", "geo", "catalog",
                "collaboration", "analytics", "integrations"
            )
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                for schema in schemas:
                    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    # Schema mutation via create_all is NEVER allowed in production.
    # In non-production, it defaults to True unless explicitly disabled.
    if settings.is_production:
        allow_create = False
    else:
        allow_raw = os.getenv("ALLOW_CREATE_ALL")
        if allow_raw is None:
            allow_create = True
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