"""Database engine, session factory, and Base — PostgreSQL on Railway."""

from sqlalchemy import create_engine, event, text
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
    Import all models so SQLAlchemy registers them, then ensure the
    required schemas/tables exist.

    The Railway deploy was crashing because startup queried
    pricing.vendors before the table existed. Creating schemas and
    running metadata.create_all() makes a fresh database bootstrappable.
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

    # Ensure schemas exist on PostgreSQL before table creation.
    if settings.is_postgres:
        schemas = ("auth", "bom", "projects", "pricing", "sourcing", "ops", "geo")
        with engine.begin() as conn:
            for schema in schemas:
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    # Safe on both SQLite and PostgreSQL; only creates missing tables.
    Base.metadata.create_all(bind=engine)
