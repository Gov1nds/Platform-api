"""
Database engine, session factory, and schema bootstrap.

Production and non-production environments should use Alembic migrations for
table creation and schema evolution. This module only ensures required schemas
exist and provides SQLAlchemy engines/sessions.

References: GAP-025 (Alembic), INFERRED-004, architecture.md CC-13
"""
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
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

engine: Engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_timeout=30,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)

_read_engine: Engine | None = None
_ReadSessionLocal: sessionmaker[Session] | None = None

if settings.READ_REPLICA_URL:
    _read_engine = create_engine(
        settings.READ_REPLICA_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        future=True,
    )
    _ReadSessionLocal = sessionmaker(
        bind=_read_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Yield a primary transactional database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_replica_db() -> Generator[Session, None, None]:
    """Yield a read session from the replica, falling back to primary."""
    factory = _ReadSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


def ensure_schemas() -> None:
    """
    Ensure all application schemas exist.

    This is safe to run before Alembic or seed commands. It does not create
    tables and does not mutate table structure.
    """
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    logger.info("Ensured database schemas exist: %s", ", ".join(SCHEMAS))


def init_db() -> None:
    """
    Initialize database prerequisites.

    Important:
    - This function only ensures schemas exist.
    - Tables, columns, constraints, and schema evolution must be handled only
      through Alembic migrations.
    - Base.metadata.create_all() is intentionally not used to avoid schema drift.
    """
    ensure_schemas()
    logger.info("Database initialized; table creation is managed by Alembic only.")