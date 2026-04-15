

"""
Phase 2B Batch 3: lane scope registry and refresh prioritization support.

Behavior:
- derives a canonical lane key from real lane activity
- registers or updates market.lane_scope_registry additively
- tracks usage, priority, and refresh cadence without redesigning the scheduler
- exposes refresh candidate ordering for tiered lane refresh orchestration
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.enrichment import LaneScopeRegistry
from app.schemas.enrichment import LaneLookupContextDTO


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().upper()
    return value[:3] if value else None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


MODE_ALIASES = {
    "ocean": "sea",
    "sea": "sea",
    "air": "air",
    "air_express": "air",
    "truck": "ground",
    "ground": "ground",
    "rail": "rail",
    "courier": "parcel",
    "parcel": "parcel",
    "express": "parcel",
}

SERVICE_LEVEL_ALIASES = {
    "economy": "economy",
    "standard": "standard",
    "priority": "priority",
    "expedited": "expedited",
    "express": "expedited",
    "same_day": "same_day",
    "deferred": "economy",
}

CADENCE_TO_DELTA = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "biweekly": timedelta(days=14),
    "monthly": timedelta(days=30),
}

PRIORITY_RANK = {"critical": 4, "high": 3, "standard": 2, "low": 1}


class LaneScopeService:
    def normalize_mode(self, value: str | None) -> str:
        probe = _normalize_text(value)
        if not probe:
            return "sea"
        key = probe.lower().replace("-", "_").replace(" ", "_")
        return MODE_ALIASES.get(key, key)

    def normalize_service_level(self, value: str | None) -> str | None:
        probe = _normalize_text(value)
        if not probe:
            return None
        key = probe.lower().replace("-", "_").replace(" ", "_")
        return SERVICE_LEVEL_ALIASES.get(key, key)

    def build_lane_key(self, *, context: LaneLookupContextDTO) -> str | None:
        origin_country = _normalize_country(context.origin_country)
        destination_country = _normalize_country(context.destination_country)
        if not origin_country or not destination_country:
            return None

        origin_region = (_normalize_text(context.origin_region) or "*").lower()
        destination_region = (_normalize_text(context.destination_region) or "*").lower()
        mode = self.normalize_mode(context.mode)
        service_level = (self.normalize_service_level(context.service_level) or "*")
        return f"{origin_country}:{origin_region}->{destination_country}:{destination_region}|{mode}|{service_level}"

    def infer_priority_tier(
        self,
        *,
        context: LaneLookupContextDTO,
        source_metadata: dict[str, Any] | None = None,
    ) -> str:
        metadata = source_metadata or {}
        for key in ("priority_tier", "lane_priority", "project_priority"):
            if metadata.get(key) not in (None, ""):
                value = str(metadata[key]).strip().lower()
                if value in PRIORITY_RANK:
                    return value

        activity_score = Decimal("0")
        if context.weight_kg not in (None, ""):
            try:
                activity_score += Decimal(str(context.weight_kg))
            except Exception:
                pass
        if context.volume_cbm not in (None, ""):
            try:
                activity_score += Decimal(str(context.volume_cbm)) * Decimal("100")
            except Exception:
                pass

        service = self.normalize_service_level(context.service_level)
        if service in {"priority", "expedited", "same_day"}:
            activity_score += Decimal("75")
        mode = self.normalize_mode(context.mode)
        if mode in {"air", "parcel"}:
            activity_score += Decimal("25")

        if activity_score >= Decimal("150"):
            return "critical"
        if activity_score >= Decimal("60"):
            return "high"
        if activity_score <= Decimal("5"):
            return "low"
        return "standard"

    def infer_refresh_cadence(self, *, priority_tier: str, mode: str, service_level: str | None) -> str:
        priority_tier = str(priority_tier or "standard").lower()
        mode = self.normalize_mode(mode)
        service_level = self.normalize_service_level(service_level)

        if priority_tier == "critical":
            return "daily"
        if priority_tier == "high" or service_level in {"priority", "expedited", "same_day"}:
            return "weekly"
        if mode in {"air", "parcel"}:
            return "weekly"
        if priority_tier == "low":
            return "monthly"
        return "biweekly"

    def get_scope_row(self, db: Session, *, lane_key: str | None) -> LaneScopeRegistry | None:
        if not lane_key:
            return None
        return db.query(LaneScopeRegistry).filter(LaneScopeRegistry.lane_key == lane_key).first()

    def register_lane_activity(
        self,
        db: Session,
        *,
        context: LaneLookupContextDTO,
        source: str = "platform-api",
        source_metadata: dict[str, Any] | None = None,
        touched_at: datetime | None = None,
    ) -> LaneScopeRegistry | None:
        lane_key = self.build_lane_key(context=context)
        if lane_key is None:
            return None

        when = touched_at or _now()
        normalized_mode = self.normalize_mode(context.mode)
        normalized_service = self.normalize_service_level(context.service_level)
        metadata = dict(source_metadata or {})
        priority_tier = self.infer_priority_tier(context=context, source_metadata=metadata)
        refresh_cadence = metadata.get("refresh_cadence") or self.infer_refresh_cadence(
            priority_tier=priority_tier,
            mode=normalized_mode,
            service_level=normalized_service,
        )

        row = self.get_scope_row(db, lane_key=lane_key)
        activity_increment = Decimal("1")
        if context.weight_kg not in (None, ""):
            try:
                activity_increment += min(Decimal("100"), Decimal(str(context.weight_kg)) / Decimal("10"))
            except Exception:
                pass

        if row is None:
            row = LaneScopeRegistry(
                lane_key=lane_key,
                origin_country=_normalize_country(context.origin_country),
                origin_region=_normalize_text(context.origin_region),
                destination_country=_normalize_country(context.destination_country),
                destination_region=_normalize_text(context.destination_region),
                mode=normalized_mode,
                service_level=normalized_service,
                scope_status=str(metadata.get("scope_status") or "observed"),
                priority_tier=priority_tier,
                refresh_cadence=str(refresh_cadence),
                activity_score=activity_increment,
                usage_count=1,
                last_used_at=when,
                source=source,
                source_metadata=metadata,
            )
            db.add(row)
        else:
            row.origin_region = _normalize_text(context.origin_region) or row.origin_region
            row.destination_region = _normalize_text(context.destination_region) or row.destination_region
            row.mode = normalized_mode or row.mode
            row.service_level = normalized_service or row.service_level
            row.scope_status = metadata.get("scope_status") or row.scope_status or "observed"
            row.priority_tier = (
                priority_tier
                if PRIORITY_RANK.get(priority_tier, 0) >= PRIORITY_RANK.get(str(row.priority_tier or "low").lower(), 0)
                else row.priority_tier
            )
            row.refresh_cadence = str(refresh_cadence or row.refresh_cadence)
            row.activity_score = Decimal(str(row.activity_score or 0)) + activity_increment
            row.usage_count = int(row.usage_count or 0) + 1
            row.last_used_at = when
            row.source = source or row.source
            row.source_metadata = {**(row.source_metadata or {}), **metadata}
            row.updated_at = _now()
        db.flush()
        return row

    def mark_lane_refreshed(
        self,
        db: Session,
        *,
        lane_key: str,
        refreshed_at: datetime | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> LaneScopeRegistry | None:
        row = self.get_scope_row(db, lane_key=lane_key)
        if row is None:
            return None
        row.last_refreshed_at = refreshed_at or _now()
        if source_metadata:
            row.source_metadata = {**(row.source_metadata or {}), **source_metadata}
        row.updated_at = _now()
        db.flush()
        return row

    def _is_due(self, row: LaneScopeRegistry, *, now: datetime) -> bool:
        cadence = str(row.refresh_cadence or "monthly").lower()
        delta = CADENCE_TO_DELTA.get(cadence, timedelta(days=30))
        if row.last_refreshed_at is None:
            return True
        return row.last_refreshed_at + delta <= now

    def list_refresh_candidates(self, db: Session, *, limit: int = 100, now: datetime | None = None) -> list[LaneScopeRegistry]:
        current = now or _now()
        rows = (
            db.query(LaneScopeRegistry)
            .filter(LaneScopeRegistry.scope_status.in_(["active", "covered", "observed"]))
            .all()
        )
        due_rows = [row for row in rows if self._is_due(row, now=current)]
        ranked = sorted(
            due_rows,
            key=lambda row: (
                PRIORITY_RANK.get(str(row.priority_tier or "low").lower(), 0),
                float(row.activity_score or 0),
                row.last_used_at.timestamp() if row.last_used_at else 0,
                -(row.last_refreshed_at.timestamp() if row.last_refreshed_at else 0),
            ),
            reverse=True,
        )
        return ranked[:limit]


lane_scope_service = LaneScopeService()