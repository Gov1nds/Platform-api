"""
Database engine, session factory, and initialization.

References: GAP-025 (Alembic), INFERRED-004, architecture.md CC-13
"""
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

SCHEMAS = (
    "auth",
    "bom",
    "projects",
    "sourcing",
    "pricing",
    "ops",
    "market",
    "finance",
    "logistics",
)

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_timeout=30,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Optional read-replica engine for analytics queries
_read_engine = None
_ReadSessionLocal = None

if settings.READ_REPLICA_URL:
    _read_engine = create_engine(
        settings.READ_REPLICA_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
    )
    _ReadSessionLocal = sessionmaker(bind=_read_engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Yield a transactional DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_replica_db() -> Generator[Session, None, None]:
    """Yield a read-only session from the replica, falling back to primary."""
    factory = _ReadSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Bootstrap database schemas.

    In non-production environments, also runs ``create_all()`` for rapid
    development. Production MUST use Alembic migrations exclusively.
    """
    with engine.connect() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()

    if settings.is_production:
        logger.info("Production mode — skipping create_all (use Alembic)")
    else:
        logger.info("Dev/test mode — running Base.metadata.create_all()")
        Base.metadata.create_all(bind=engine)