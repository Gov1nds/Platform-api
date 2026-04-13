from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import Table, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass(slots=True)
class SeedStats:
    name: str
    inserted: int = 0
    updated: int = 0

    def merge(self, other: "SeedStats") -> "SeedStats":
        self.inserted += other.inserted
        self.updated += other.updated
        return self


class SeedError(RuntimeError):
    pass


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def ensure_list(value: Any) -> list[Any]:
    if value in (None, "", "null"):
        return []
    if isinstance(value, list):
        return value
    return [value]


def ensure_dict(value: Any) -> dict[str, Any]:
    if value in (None, "", "null"):
        return {}
    if isinstance(value, dict):
        return value
    raise SeedError(f"Expected dict-compatible value, got: {type(value)!r}")


def load_records(seed_root: Path, relative_path: str) -> list[dict[str, Any]]:
    json_path = seed_root / relative_path
    if not json_path.exists():
        raise SeedError(f"Seed file not found: {json_path}")
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise SeedError(f"Seed file must contain a list: {json_path}")
    return payload


def create_seed_tables(engine) -> None:
    from app.seeds.tables import seed_metadata

    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS reference")
        seed_metadata.create_all(bind=conn, checkfirst=True)


def _existing_key_map(
    db: Session,
    table: Table,
    key_columns: Sequence[str],
    keys: Iterable[tuple[Any, ...]],
) -> set[tuple[Any, ...]]:
    key_list = list(keys)
    if not key_list:
        return set()

    cols = [table.c[name] for name in key_columns]
    stmt = select(*cols)
    if len(cols) == 1:
        stmt = stmt.where(cols[0].in_([key[0] for key in key_list]))
    else:
        stmt = stmt.where(tuple_(*cols).in_(key_list))

    return {tuple(row) for row in db.execute(stmt).all()}


def upsert_table(
    db: Session,
    table: Table,
    rows: list[dict[str, Any]],
    key_columns: Sequence[str],
    stat_name: str,
) -> SeedStats:
    stats = SeedStats(name=stat_name)
    if not rows:
        return stats

    keys = [tuple(row[column] for column in key_columns) for row in rows]
    existing = _existing_key_map(db, table, key_columns, keys)

    insert_stmt = pg_insert(table).values(rows)
    update_columns = {
        column.name: insert_stmt.excluded[column.name]
        for column in table.columns
        if column.name not in set(key_columns)
    }
    db.execute(insert_stmt.on_conflict_do_update(index_elements=list(key_columns), set_=update_columns))

    for key in keys:
        if key in existing:
            stats.updated += 1
        else:
            stats.inserted += 1

    logger.info("seeded %s | inserted=%s updated=%s", stat_name, stats.inserted, stats.updated)
    return stats
