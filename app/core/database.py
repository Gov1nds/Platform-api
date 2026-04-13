"""
Database engine, session factory, and initialization.

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

if getattr(settings, "READ_REPLICA_URL", None):
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


def ensure_schemas() -> None:
    """Ensure all application schemas exist."""
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    logger.info("Ensured database schemas exist: %s", ", ".join(SCHEMAS))


def init_db() -> None:
    """
    Bootstrap database for a fresh environment.

    Current mode:
    - Ensures schemas exist
    - Creates all ORM tables from metadata

    This is intended for initial bootstrap on a fresh database so seeds can run.
    Once bootstrap is complete, switch back to Alembic-only table management.
    """
    ensure_schemas()
    Base.metadata.create_all(bind=engine)
    logger.info("Bootstrap mode — created tables from ORM metadata.")