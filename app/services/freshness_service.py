"""
Data freshness service — Blueprint §23.3, §23.4, C8.

Tracks TTL for market data. Writes DataFreshnessLog on each refresh.
Provides annotate_freshness() and require_fresh() decorator.
"""
from __future__ import annotations
import enum, logging, uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class FreshnessStatus(str, enum.Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    ESTIMATED = "ESTIMATED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

TTL_MAP: dict[str, int] = {
    "fx_rates": 15,
    "freight_rates": 1440,
    "tariff_schedules": 10080,
    "commodity_indices": 1440,
    "baseline_price": 1440,
    "baseline_prices": 1440,
    "logistics_rate": 1440,
    "vendor_performance_snapshots": 1440,
}

def log_refresh(db, *, table_name: str, record_id: str, source_api: str,
                status: str, previous_value_json: dict | None = None,
                new_value_json: dict | None = None,
                error_message: str | None = None, duration_ms: int | None = None):
    """Write a row to data_freshness_log."""
    from app.models.data_freshness import DataFreshnessLog
    row = DataFreshnessLog(
        table_name=table_name, record_id=record_id,
        source_api=source_api, status=status,
        previous_value_json=previous_value_json,
        new_value_json=new_value_json,
        error_message=error_message, duration_ms=duration_ms,
    )
    db.add(row)
    return row

def annotate_freshness(payload: dict, sources: list[str]) -> dict:
    """Attach a freshness_report dict to any response."""
    from sqlalchemy import text
    from app.core.database import SessionLocal
    report = {}
    try:
        with SessionLocal() as db:
            for src in sources:
                row = db.execute(text("""
                    SELECT fetched_at, status, source_api FROM data_freshness_log
                    WHERE table_name = :t ORDER BY fetched_at DESC LIMIT 1
                """), {"t": src}).first()
                if row:
                    age = (datetime.now(timezone.utc) - row.fetched_at).total_seconds()
                    ttl = TTL_MAP.get(src, 1440) * 60
                    st = "FRESH" if age < ttl else ("STALE" if age < 2 * ttl else "EXPIRED")
                    report[src] = {"fetched_at": row.fetched_at.isoformat(), "status": st,
                                   "age_seconds": int(age), "source_api": row.source_api}
                else:
                    report[src] = {"status": "ESTIMATED", "source_api": "none"}
    except Exception:
        logger.debug("freshness annotation failed", exc_info=True)
    payload["freshness_report"] = report
    payload["computed_at"] = datetime.now(timezone.utc).isoformat()
    return payload

def require_fresh(*source_tables):
    """Decorator: annotate handler response with freshness data."""
    def deco(fn):
        @wraps(fn)
        async def inner(*args, **kwargs):
            result = await fn(*args, **kwargs) if _is_async(fn) else fn(*args, **kwargs)
            if isinstance(result, dict):
                annotate_freshness(result, list(source_tables))
            return result
        return inner
    return deco

def _is_async(fn):
    import asyncio
    return asyncio.iscoroutinefunction(fn)

class FreshnessService:
    def check(self, db: Session, table_name: str, record_id: str) -> FreshnessStatus:
        ttl = TTL_MAP.get(table_name, 1440)
        try:
            from sqlalchemy import text
            row = db.execute(text(
                "SELECT fetched_at, status FROM data_freshness_log "
                "WHERE table_name = :t AND record_id = :r ORDER BY fetched_at DESC LIMIT 1"
            ), {"t": table_name, "r": record_id}).first()
            if not row:
                return FreshnessStatus.UNKNOWN
            age = (datetime.now(timezone.utc) - row.fetched_at).total_seconds()
            if age > ttl * 60:
                return FreshnessStatus.STALE
            return FreshnessStatus(row.status) if row.status else FreshnessStatus.UNKNOWN
        except Exception:
            return FreshnessStatus.UNKNOWN

    def mark_fetched(self, db, table_name, record_id, source_api, success, prev_value=None, new_value=None):
        log_refresh(db, table_name=table_name, record_id=record_id,
                    source_api=source_api, status="success" if success else "error",
                    previous_value_json={"v": str(prev_value)[:500]} if prev_value else None,
                    new_value_json={"v": str(new_value)[:500]} if new_value else None)

    def is_fresh(self, record) -> bool:
        status = getattr(record, "freshness_status", None)
        if status == "FRESH":
            fetched_at = getattr(record, "fetched_at", None)
            ttl_seconds = getattr(record, "ttl_seconds", 900)
            if fetched_at:
                age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
                return age < ttl_seconds
            return True
        return False

    def build_freshness_report(self, db, tables=None):
        tables = tables or list(TTL_MAP.keys())
        report = {}
        for tbl in tables:
            report[tbl] = {"ttl_minutes": TTL_MAP.get(tbl, 1440), "status": "UNKNOWN"}
        return report

freshness_service = FreshnessService()
