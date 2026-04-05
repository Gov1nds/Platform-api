"""Alembic env.py — wired to PGI platform-api database config and models."""
import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import Base

# Import ALL models so Base.metadata is fully populated
import app.models.user
import app.models.project
import app.models.bom
import app.models.analysis
import app.models.vendor
import app.models.vendor_match
import app.models.pricing
import app.models.rfq
import app.models.tracking
import app.models.memory
import app.models.drawing
import app.models.catalog
import app.models.geo
import app.models.report_snapshot
import app.models.strategy_run
import app.models.collaboration
import app.models.analytics
import app.models.workflow_command
import app.models.intake
import app.models.project_access
import app.models.integration_assets
import app.models.organization

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

INCLUDE_SCHEMAS = {
    "auth", "bom", "projects", "pricing",
    "sourcing", "ops", "geo", "catalog",
    "collaboration", "analytics", "integrations", "orgs",
}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        schema = getattr(object, "schema", None)
        if schema and schema not in INCLUDE_SCHEMAS:
            return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        include_schemas=True, include_object=include_object,
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        for schema in sorted(INCLUDE_SCHEMAS):
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.commit()
        context.configure(
            connection=connection, target_metadata=target_metadata,
            include_schemas=True, include_object=include_object,
            version_table_schema="public",
            compare_type=True, compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
