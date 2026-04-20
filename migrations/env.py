"""Alembic environment configuration.

Supports both offline (SQL script) and online (direct DB) migration modes.
Uses the SQLAlchemy MetaData from the ORM models for autogenerate support.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Base metadata so autogenerate picks up ORM models
from app.models.base import metadata_obj  # noqa: F401 – side-effects register models

# Import all models so metadata is fully populated
import app.models  # noqa: F401

config = context.config

# Honour ini-file logging config when present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata_obj


def get_url() -> str:
    """Prefer DATABASE_URL env var over alembic.ini sqlalchemy.url."""
    return os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url", ""))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout/file)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()