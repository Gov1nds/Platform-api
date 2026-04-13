from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import NotificationPreference
from app.models.user import User
from app.seeds.base import SeedStats, ensure_dict, ensure_list, load_records, upsert_table
from app.seeds.tables import (
    category_taxonomy,
    certification_types,
    countries_currencies,
    incoterms,
    manufacturing_processes,
    material_families,
    notification_event_types,
    shipping_modes,
    unit_catalog,
)

logger = logging.getLogger(__name__)


def load_reference(seed_root, db: Session) -> list[SeedStats]:
    stats = [
        upsert_table(
            db,
            countries_currencies,
            load_records(seed_root, "reference/countries_currencies.json"),
            ["country_code"],
            "reference.countries_currencies",
        ),
        upsert_table(
            db,
            manufacturing_processes,
            load_records(seed_root, "reference/manufacturing_processes.json"),
            ["process_code"],
            "reference.manufacturing_processes",
        ),
        upsert_table(
            db,
            material_families,
            [
                {
                    "material_family": row["material_family"],
                    "examples": ensure_list(row.get("examples")),
                }
                for row in load_records(seed_root, "reference/material_families.json")
            ],
            ["material_family"],
            "reference.material_families",
        ),
        upsert_table(
            db,
            category_taxonomy,
            [
                {
                    "taxonomy_code": row["taxonomy_code"],
                    "taxonomy_path": row["taxonomy_path"],
                    "commodity_group": row.get("commodity_group"),
                    "hs_code_default": row.get("hs_code_default"),
                    "unit_of_measure_options": ensure_list(row.get("unit_of_measure_options")),
                    "spec_schema_json": ensure_dict(row.get("spec_schema_json")),
                    "model_version": row.get("model_version"),
                    "data_source": row.get("data_source"),
                    "is_active": bool(row.get("is_active", True)),
                    "version": int(row.get("version") or 1),
                }
                for row in load_records(seed_root, "reference/category_taxonomy.json")
            ],
            ["taxonomy_code"],
            "reference.category_taxonomy",
        ),
        upsert_table(
            db,
            unit_catalog,
            [
                {
                    "unit_code": row["unit_code"],
                    "display_name": row["display_name"],
                    "aliases": ensure_list(row.get("aliases")),
                }
                for row in load_records(seed_root, "reference/unit_catalog.json")
            ],
            ["unit_code"],
            "reference.unit_catalog",
        ),
        upsert_table(
            db,
            incoterms,
            load_records(seed_root, "reference/incoterms.json"),
            ["incoterm_code"],
            "reference.incoterms",
        ),
        upsert_table(
            db,
            shipping_modes,
            load_records(seed_root, "reference/shipping_modes.json"),
            ["mode"],
            "reference.shipping_modes",
        ),
        upsert_table(
            db,
            certification_types,
            [
                {
                    "cert_type_code": row["cert_type_code"],
                    "display_name": row["display_name"],
                    "category": row.get("category"),
                    "requires_expiry": bool(row.get("requires_expiry", False)),
                }
                for row in load_records(seed_root, "reference/certification_types.json")
            ],
            ["cert_type_code"],
            "reference.certification_types",
        ),
        upsert_table(
            db,
            notification_event_types,
            [
                {
                    "event_type": row["event_type"],
                    "channels": ensure_list(row.get("channels")),
                }
                for row in load_records(seed_root, "reference/notification_event_types.json")
            ],
            ["event_type"],
            "reference.notification_event_types",
        ),
    ]
    stats.append(_sync_notification_preferences(db))
    return stats


def _sync_notification_preferences(db: Session) -> SeedStats:
    stats = SeedStats(name="reference.notification_preferences")
    event_types = [row[0] for row in db.execute(select(notification_event_types.c.event_type)).all()]
    users = db.execute(select(User)).scalars().all()

    for user in users:
        metadata = dict(user.metadata_ or {})
        defaults = metadata.get("notification_defaults") or {}
        for event_type in event_types:
            pref = db.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user.id,
                    NotificationPreference.notification_type == event_type,
                )
            ).scalar_one_or_none()
            if pref is None:
                pref = NotificationPreference(
                    user_id=user.id,
                    notification_type=event_type,
                )
                db.add(pref)
                stats.inserted += 1
            else:
                stats.updated += 1

            pref.channel_email = bool(defaults.get("email", True))
            pref.channel_sms = bool(defaults.get("sms", False))
            pref.channel_push = bool(defaults.get("push", True))
            pref.channel_in_app = bool(defaults.get("in_app", True))

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats
