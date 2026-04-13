from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.notification import NotificationPreference
from app.models.user import Organization, OrganizationMembership, User
from app.seeds.base import SeedError, SeedStats, ensure_dict, load_records, parse_datetime

logger = logging.getLogger(__name__)


def load_platform(seed_root, db: Session) -> list[SeedStats]:
    return [
        _load_organizations(seed_root, db),
        _load_admin_users(seed_root, db),
    ]


def _load_organizations(seed_root, db: Session) -> SeedStats:
    records = load_records(seed_root, "platform/platform_org.json")
    stats = SeedStats(name="platform.organizations")

    for row in records:
        org = db.execute(
            select(Organization).where(Organization.slug == row["slug"])
        ).scalar_one_or_none()

        settings_json = ensure_dict(row.get("settings_json"))
        settings_json.update(
            {
                "plan_tier": row.get("plan_tier"),
                "country_code": row.get("country_code"),
                "preferred_currency": row.get("preferred_currency"),
                "billing_email": row.get("billing_email"),
                "address": ensure_dict(row.get("address")),
                "seed_source": "phase1_seed_assets",
                "notification_defaults": dict(row.get("notification_preferences") or {}),
            }
        )

        if org is None:
            org = Organization(
                id=row["organization_id"],
                slug=row["slug"],
            )
            stats.inserted += 1
            db.add(org)
        else:
            stats.updated += 1

        org.name = row["name"]
        org.type = row.get("type") or org.type
        org.settings_json = settings_json
        org.created_at = parse_datetime(row.get("created_at")) or org.created_at
        org.updated_at = parse_datetime(row.get("updated_at")) or org.updated_at
        org.deleted_at = parse_datetime(row.get("deleted_at"))

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _load_admin_users(seed_root, db: Session) -> SeedStats:
    records = load_records(seed_root, "platform/platform_admin_user.json")
    stats = SeedStats(name="platform.admin_users")

    for row in records:
        user = db.execute(select(User).where(User.email == row["email"])).scalar_one_or_none()
        password_env = row.get("seed_runtime_password_env")
        password_value = os.getenv(password_env or "")
        if not password_value:
            raise SeedError(
                f"Missing required admin password env var: {password_env}. "
                "Export it before running seeds."
            )

        if user is None:
            user = User(id=row["user_id"], email=row["email"])
            stats.inserted += 1
            db.add(user)
        else:
            stats.updated += 1

        user.full_name = row.get("full_name") or ""
        user.role = (row.get("role") or "BUYER_EDITOR").upper()
        user.organization_id = row.get("organization_id")
        user.password_hash = hash_password(password_value)
        user.is_active = True
        user.is_verified = bool(row.get("email_verified", False))
        user.mfa_enabled = bool(row.get("mfa_enabled", False))
        user.permissions = ["platform:admin"] if user.role == "PGI_ADMIN" else []

        metadata = dict(user.metadata_ or {})
        metadata.update(
            {
                "preferred_currency": row.get("preferred_currency"),
                "preferred_language": row.get("preferred_language"),
                "converted_from_guest_session_id": row.get("converted_from_guest_session_id"),
                "seed_source": "phase1_seed_assets",
                "notification_defaults": dict(row.get("notification_preferences") or {}),
            }
        )
        user.metadata_ = metadata
        user.created_at = parse_datetime(row.get("created_at")) or user.created_at
        user.updated_at = parse_datetime(row.get("updated_at")) or user.updated_at
        user.deleted_at = parse_datetime(row.get("deleted_at"))

        membership = db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == row.get("organization_id"),
                OrganizationMembership.user_id == user.id,
            )
        ).scalar_one_or_none()
        if membership is None:
            db.add(
                OrganizationMembership(
                    organization_id=row.get("organization_id"),
                    user_id=user.id,
                    role=user.role,
                    accepted_at=parse_datetime(row.get("updated_at")),
                )
            )

        pref = row.get("notification_preferences") or {}
        for notification_type in ("SYSTEM",):
            existing_pref = db.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user.id,
                    NotificationPreference.notification_type == notification_type,
                )
            ).scalar_one_or_none()
            if existing_pref is None:
                existing_pref = NotificationPreference(
                    user_id=user.id,
                    notification_type=notification_type,
                )
                db.add(existing_pref)

            existing_pref.channel_email = bool(pref.get("email", True))
            existing_pref.channel_sms = bool(pref.get("sms", False))
            existing_pref.channel_push = bool(pref.get("push", True))
            existing_pref.channel_in_app = bool(pref.get("in_app", True))

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats
