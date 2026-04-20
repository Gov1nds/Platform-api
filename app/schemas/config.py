"""
config.py
─────────────────────────────────────────────────────────────────────────────
PGI Hub — Platform Configuration Schema Layer

CONTRACT AUTHORITY: contract.md §2.77 (Config_Version), §2.78 (Feature_Flag).

Invariants:
  • ConfigVersion: UNIQUE (config_type, version).
  • FeatureFlag.key: globally UNIQUE.
  • FeatureFlag.scope: global | organization | user.
  • Server-side feature flags gate by billing plan (Repo C is authoritative).
  • Repo A may display visual hints from feature flags but enforcement
    is always on Repo C (§1.2, §6 Ownership Rules).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from .common import ConfigType, FeatureFlagScope, PGIBase, TIMESTAMPTZ


# ──────────────────────────────────────────────────────────────────────────
# Config_Version (contract §2.77)
# ──────────────────────────────────────────────────────────────────────────

class ConfigVersionSchema(PGIBase):
    """Version record for a system configuration artifact.

    config_type: nlp_model | scoring_model | weight_profile_defaults |
                 approval_thresholds.
    UNIQUE: (config_type, version).
    deprecated_at: when non-null, this version should no longer be used.
    """

    version_id: UUID
    config_type: ConfigType
    version: str = Field(max_length=32)
    effective_at: TIMESTAMPTZ
    deprecated_at: Optional[datetime] = None


class ConfigVersionCreateRequest(PGIBase):
    """Register a new configuration version."""

    config_type: ConfigType
    version: str = Field(min_length=1, max_length=32)
    effective_at: TIMESTAMPTZ


class ConfigVersionDeprecateRequest(PGIBase):
    """Mark a configuration version as deprecated."""

    deprecated_at: TIMESTAMPTZ


class ConfigVersionListResponse(PGIBase):
    """All configuration versions for a given config_type."""

    config_type: ConfigType
    versions: list[ConfigVersionSchema]


# ──────────────────────────────────────────────────────────────────────────
# Feature_Flag (contract §2.78)
# ──────────────────────────────────────────────────────────────────────────

class FeatureFlagSchema(PGIBase):
    """A server-side feature flag controlling product functionality.

    key: globally unique string identifier (e.g. 'enable_ai_substitution',
    'enable_3way_match_auto_approve').
    scope: global (all), organization (specific orgs), user (specific users).
    value_json: arbitrary JSON value (bool, number, list, object).
    """

    flag_id: UUID
    key: str = Field(max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    scope: FeatureFlagScope
    value_json: Any
    updated_by: UUID
    updated_at: datetime


class FeatureFlagCreateRequest(PGIBase):
    """Create or upsert a feature flag."""

    key: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    scope: FeatureFlagScope
    value_json: Any = Field(description="Arbitrary JSON value for the flag.")


class FeatureFlagUpdateRequest(PGIBase):
    """Update a feature flag value or scope."""

    scope: Optional[FeatureFlagScope] = None
    value_json: Optional[Any] = None
    description: Optional[str] = Field(default=None, max_length=512)


class FeatureFlagListResponse(PGIBase):
    """All feature flags."""

    items: list[FeatureFlagSchema]
