"""Database engine, session factory, and Base."""

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
    echo=False,
)


# -------------------------------------------------------------------
# SQLite Optimizations
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
    Create all tables.

    NOTE:
    - This uses SQLAlchemy metadata.
    - Replace with Alembic migrations in production.
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

    Base.metadata.create_all(bind=engine)