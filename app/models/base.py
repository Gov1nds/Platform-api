"""
SQLAlchemy 2.0 declarative Base + common mixins + shared column-type factories.

This module is the structural plumbing for the entire models layer. Every ORM
class in ``app/models/*.py`` inherits :class:`Base` from here.

Contract anchors
----------------
§2.1  — All primary keys are UUID; all timestamps are TIMESTAMPTZ;
        all monetary values are DECIMAL(20, 8); every FK is ON DELETE
        RESTRICT unless otherwise stated; every JSON field is JSONB;
        every table has ``created_at``; every user-visible entity also has
        ``updated_at`` and ``deleted_at``.
§2.92 — Every non-snapshot entity carries at least one non-null owner link.
§7.2  — Canonical column names (``created_at``, ``updated_at``, ``deleted_at``,
        ``organization_id``, ``vendor_id``, ``user_id``, ``created_by``,
        ``assigned_to``, ``idempotency_key``, ``freshness_status`` …).

Hard rules enforced here
------------------------
* No call to ``Base.metadata.create_all()`` exists anywhere in the codebase.
  Alembic owns every schema mutation.
* Foreign keys constructed via :func:`uuid_fk` require an explicit ``ondelete``
  argument — defaults are prohibited to prevent accidental cascade mistakes.
* A deterministic naming convention is registered on :class:`MetaData` so
  Alembic autogenerate produces stable constraint/index names.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# MetaData with a deterministic naming convention. This lets Alembic generate
# stable constraint / index names across autogeneration runs and avoids
# accidental collisions on entities that share column names (very common in
# a 100+ table schema).
# ---------------------------------------------------------------------------
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj: MetaData = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Root declarative class. Every ORM model in this package inherits it.

    * Concrete subclasses set ``__tablename__`` explicitly (singular snake_case).
    * Never call ``Base.metadata.create_all()`` — Alembic owns all DDL.
    """

    metadata = metadata_obj
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }

    def __repr__(self) -> str:
        pk_cols = self.__mapper__.primary_key
        parts = ", ".join(
            f"{col.key}={getattr(self, col.key, '?')!r}" for col in pk_cols
        )
        return f"<{self.__class__.__name__}({parts})>"


# ---------------------------------------------------------------------------
# Column-type factories — keep signatures uniform across 100+ tables and
# guarantee we never diverge on a PK/FK/money/currency column definition.
# ---------------------------------------------------------------------------

_ALLOWED_ONDELETE: frozenset[str] = frozenset({"RESTRICT", "CASCADE", "SET NULL"})


def uuid_pk() -> Any:
    """UUID primary key column backed by server-side ``gen_random_uuid()``.

    The underlying Postgres function is provided by the ``pgcrypto`` extension
    (enabled in the initial Alembic migration).
    """
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )


def uuid_fk(
    target: str,
    *,
    ondelete: str,
    nullable: bool = False,
    index: bool = False,
    unique: bool = False,
    use_alter: bool = False,
    name: str | None = None,
) -> Any:
    """UUID foreign-key column. ``ondelete`` MUST be explicit.

    Parameters
    ----------
    target:
        ``'<table>.<column>'`` string passed to :class:`ForeignKey`.
    ondelete:
        One of ``"RESTRICT"``, ``"CASCADE"``, ``"SET NULL"``. Required.
    nullable:
        Whether the column is nullable.
    index:
        Whether to emit a single-column index on the FK.
    unique:
        Whether the FK column has a UNIQUE constraint (1:1 relationships).
    use_alter:
        Emit the FK with ``ALTER TABLE`` after table creation. Required for
        cyclic references (e.g. ``organization.preferred_vendor_list_id``
        ↔ ``preferred_vendor_list.organization_id``).
    name:
        Optional explicit FK constraint name (needed with ``use_alter=True``).
    """
    if ondelete not in _ALLOWED_ONDELETE:
        raise ValueError(
            f"Unsupported ondelete={ondelete!r}; allowed: {sorted(_ALLOWED_ONDELETE)}"
        )
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(target, ondelete=ondelete, use_alter=use_alter, name=name),
        nullable=nullable,
        index=index,
        unique=unique,
    )


def uuid_polymorphic(*, nullable: bool = False, primary_key: bool = False) -> Any:
    """UUID column for polymorphic pointers that cannot use a typed FK."""
    return mapped_column(PG_UUID(as_uuid=True), nullable=nullable, primary_key=primary_key)


def tstz(
    *,
    nullable: bool = False,
    default_now: bool = False,
    on_update: bool = False,
) -> Any:
    """TIMESTAMPTZ column helper for every ad-hoc datetime column."""
    kwargs: dict[str, Any] = {"nullable": nullable}
    if default_now:
        kwargs["server_default"] = text("now()")
    if on_update:
        kwargs["onupdate"] = text("now()")
    return mapped_column(DateTime(timezone=True), **kwargs)


def money() -> Any:
    """DECIMAL(20, 8) monetary column — NOT NULL, caller supplies value."""
    return mapped_column(Numeric(20, 8), nullable=False)


def money_default_zero() -> Any:
    """DECIMAL(20, 8) monetary column — NOT NULL, server default 0."""
    return mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))


def money_nullable() -> Any:
    """DECIMAL(20, 8) monetary column — nullable."""
    return mapped_column(Numeric(20, 8), nullable=True)


def currency_code() -> Any:
    """ISO-4217 VARCHAR(3) currency column — NOT NULL."""
    return mapped_column(String(3), nullable=False)


def currency_code_nullable() -> Any:
    """ISO-4217 VARCHAR(3) currency column — nullable."""
    return mapped_column(String(3), nullable=True)


def country_code() -> Any:
    """ISO-3166 alpha-2 VARCHAR(2) country column — NOT NULL."""
    return mapped_column(String(2), nullable=False)


def country_code_nullable() -> Any:
    """ISO-3166 alpha-2 VARCHAR(2) country column — nullable."""
    return mapped_column(String(2), nullable=True)


def jsonb_object() -> Any:
    """NOT NULL JSONB column defaulting to empty object ``'{}'``."""
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


def jsonb_array() -> Any:
    """NOT NULL JSONB column defaulting to empty array ``'[]'``."""
    return mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


def jsonb_object_nullable() -> Any:
    """Nullable JSONB column."""
    return mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------

# Some contract entities intentionally use domain-specific creation timestamps
# instead of CreatedAtMixin.created_at; NormalizationRun.started_at is the
# approved creation-time column for that table.

class CreatedAtMixin:
    """Adds ``created_at`` only — used by append-only / event / log tables."""

    created_at: Mapped[datetime] = tstz(default_now=True)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at``.

    Contract §2.1: every user-visible entity has both, plus ``deleted_at``
    (supplied by :class:`SoftDeleteMixin`).
    """

    created_at: Mapped[datetime] = tstz(default_now=True)
    updated_at: Mapped[datetime] = tstz(default_now=True, on_update=True)


class SoftDeleteMixin:
    """Adds ``deleted_at`` column (NULL when the row is live)."""

    deleted_at: Mapped[datetime | None] = tstz(nullable=True)


# ---------------------------------------------------------------------------
# Constraint helpers
# ---------------------------------------------------------------------------

def enum_check(column_name: str, allowed: tuple[str, ...] | list[str]) -> CheckConstraint:
    """Emit a CHECK constraint enumerating allowed values for a VARCHAR status column.

    Contract §2.1: enumerated columns use VARCHAR + CHECK (no PostgreSQL ENUM
    TYPE) to allow zero-downtime value additions via expand/contract migrations.

    The constraint name is derived from ``column_name`` so it is stable and
    discoverable in migrations (prefix ``ck_<table>_<column>_enum``).
    """
    quoted = ", ".join(f"'{v}'" for v in allowed)
    return CheckConstraint(
        f"{column_name} IN ({quoted})",
        name=f"{column_name}_enum",
    )


def nullable_enum_check(
    column_name: str, allowed: tuple[str, ...] | list[str]
) -> CheckConstraint:
    """Same as :func:`enum_check` but admits NULL explicitly for nullable columns."""
    quoted = ", ".join(f"'{v}'" for v in allowed)
    return CheckConstraint(
        f"{column_name} IS NULL OR {column_name} IN ({quoted})",
        name=f"{column_name}_enum",
    )


# Re-exports — one canonical source for ``uuid.UUID`` Python type hints
# across the models layer.
UUIDType = _uuid.UUID

__all__ = [
    "Base",
    "metadata_obj",
    "CreatedAtMixin",
    "TimestampMixin",
    "SoftDeleteMixin",
    "uuid_pk",
    "uuid_fk",
    "uuid_polymorphic",
    "tstz",
    "money",
    "money_default_zero",
    "money_nullable",
    "currency_code",
    "currency_code_nullable",
    "country_code",
    "country_code_nullable",
    "jsonb_object",
    "jsonb_array",
    "jsonb_object_nullable",
    "enum_check",
    "nullable_enum_check",
    "UUIDType",
    # Re-exports to reduce per-file import noise
    "Mapped",
    "mapped_column",
    "Decimal",
    "datetime",
]
