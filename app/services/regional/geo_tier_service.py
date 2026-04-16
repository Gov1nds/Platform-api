"""
Geo Tier Service.

Implements Execution Plan §3 (Regional & Global Sourcing Strategy) geo
bucketing logic. Given a requester location, classifies vendors into
local / regional / national / global buckets with per-tier logistics
profiles (shipping mode, transit days, freight cost estimate).

All prices in Decimal; transit-time estimates in integer days.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.market import FreightRate

logger = logging.getLogger(__name__)


# Indian-state neighbor map (ISO 3166-2:IN codes). Used when requester_country
# is "IN" to define the "regional" tier.
INDIA_NEIGHBORS: dict[str, list[str]] = {
    "KL": ["TN", "KA"],                    # Kerala
    "TN": ["KL", "KA", "AP", "PY"],        # Tamil Nadu
    "KA": ["KL", "TN", "MH", "GA", "TG", "AP"],  # Karnataka
    "AP": ["TN", "KA", "TG", "OR"],        # Andhra Pradesh
    "TG": ["MH", "KA", "AP", "CT"],        # Telangana
    "MH": ["GJ", "MP", "CT", "TG", "KA", "GA"],  # Maharashtra
    "GJ": ["RJ", "MP", "MH"],              # Gujarat
    "DL": ["HR", "UP", "RJ"],              # Delhi
    "HR": ["DL", "PB", "HP", "UK", "UP", "RJ"],
    "UP": ["UK", "HP", "HR", "DL", "RJ", "MP", "CT", "JH", "BR"],
    "RJ": ["PB", "HR", "DL", "UP", "MP", "GJ"],
    "PB": ["JK", "HP", "HR", "RJ"],
    "HP": ["JK", "PB", "HR", "UK"],
    "JK": ["HP", "PB"],
    "UK": ["HP", "HR", "UP"],
    "MP": ["RJ", "UP", "CT", "MH", "GJ"],
    "CT": ["UP", "JH", "OR", "MP", "MH", "TG", "AP"],
    "JH": ["BR", "UP", "CT", "OR", "WB"],
    "BR": ["UP", "JH", "WB"],
    "WB": ["JH", "BR", "OR", "SK", "AS"],
    "OR": ["JH", "WB", "CT", "AP"],
    "AS": ["AR", "NL", "ML", "MN", "MZ", "TR", "WB"],
    "AR": ["AS", "NL"],
    "NL": ["AS", "AR", "MN"],
    "MN": ["AS", "NL", "MZ"],
    "MZ": ["AS", "MN", "TR"],
    "TR": ["AS", "MZ"],
    "ML": ["AS"],
    "SK": ["WB"],
    "GA": ["MH", "KA"],
    "PY": ["TN"],
}

# Regional trade-zone hints (informational only; scoring handles the rest)
TRADE_ZONES: dict[str, str] = {
    "IN": "SAARC",
    "BD": "SAARC", "BT": "SAARC", "LK": "SAARC", "NP": "SAARC", "PK": "SAARC", "MV": "SAARC",
    "SG": "ASEAN", "MY": "ASEAN", "TH": "ASEAN", "VN": "ASEAN", "ID": "ASEAN",
    "PH": "ASEAN", "KH": "ASEAN", "LA": "ASEAN", "MM": "ASEAN", "BN": "ASEAN",
    "DE": "EU", "FR": "EU", "IT": "EU", "ES": "EU", "NL": "EU", "BE": "EU",
    "US": "USMCA", "MX": "USMCA", "CA": "USMCA",
    "CN": "ASIA_PACIFIC", "JP": "ASIA_PACIFIC", "KR": "ASIA_PACIFIC", "TW": "ASIA_PACIFIC",
}


@dataclass
class GeoContext:
    country_iso2: str | None = None
    state_province: str | None = None
    city: str | None = None
    local_state: str | None = None
    regional_states: list[str] = field(default_factory=list)
    national_country: str | None = None
    trade_zone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_iso2": self.country_iso2,
            "state_province": self.state_province,
            "city": self.city,
            "local_state": self.local_state,
            "regional_states": list(self.regional_states),
            "national_country": self.national_country,
            "trade_zone": self.trade_zone,
        }


@dataclass
class GeoBuckets:
    local: list[dict[str, Any]] = field(default_factory=list)
    regional: list[dict[str, Any]] = field(default_factory=list)
    national: list[dict[str, Any]] = field(default_factory=list)
    global_: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "local": len(self.local),
            "regional": len(self.regional),
            "national": len(self.national),
            "global": len(self.global_),
        }


@dataclass
class LogisticsProfile:
    shipping_mode: str
    est_transit_days_min: int
    est_transit_days_max: int
    est_freight_cost_usd: Decimal
    confidence: float
    geo_tier: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "shipping_mode": self.shipping_mode,
            "est_transit_days_min": self.est_transit_days_min,
            "est_transit_days_max": self.est_transit_days_max,
            "est_freight_cost_usd": str(self.est_freight_cost_usd),
            "confidence": round(self.confidence, 4),
            "geo_tier": self.geo_tier,
        }


def _norm_state_code(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip().upper()
    # Accept either 2-letter iso subdivision like "KL" or spelled names
    spelled = {
        "KERALA": "KL", "TAMIL NADU": "TN", "KARNATAKA": "KA",
        "MAHARASHTRA": "MH", "DELHI": "DL", "GUJARAT": "GJ",
        "ANDHRA PRADESH": "AP", "TELANGANA": "TG", "RAJASTHAN": "RJ",
        "PUNJAB": "PB", "UTTAR PRADESH": "UP", "WEST BENGAL": "WB",
        "HARYANA": "HR", "GOA": "GA",
    }
    if v in spelled:
        return spelled[v]
    return v[:2] if len(v) >= 2 else v


def _norm_country(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip().upper()
    spelled = {
        "INDIA": "IN",
        "UNITED STATES": "US", "USA": "US",
        "UNITED KINGDOM": "GB", "UK": "GB",
        "CHINA": "CN",
        "GERMANY": "DE",
        "VIETNAM": "VN",
        "JAPAN": "JP",
    }
    if v in spelled:
        return spelled[v]
    return v[:2] if len(v) >= 2 else v


class GeoTierService:
    """Bucket vendors by geographic tier relative to a requester location."""

    def classify_requester_location(self, location_input: dict[str, Any]) -> GeoContext:
        country = _norm_country(
            location_input.get("country_iso2") or location_input.get("country")
        )
        state = _norm_state_code(
            location_input.get("state_province")
            or location_input.get("state")
            or location_input.get("region")
        )
        city = location_input.get("city")

        regional_states: list[str] = []
        if country == "IN" and state:
            regional_states = list(INDIA_NEIGHBORS.get(state, []))

        ctx = GeoContext(
            country_iso2=country,
            state_province=state,
            city=city,
            local_state=state,
            regional_states=regional_states,
            national_country=country,
            trade_zone=TRADE_ZONES.get(country) if country else None,
        )
        return ctx

    def _classify_one(
        self,
        vendor: dict[str, Any],
        geo_ctx: GeoContext,
    ) -> str:
        v_country = _norm_country(vendor.get("country") or vendor.get("country_iso2"))
        # Prefer primary location state if available
        v_state: str | None = _norm_state_code(vendor.get("region"))
        locs = vendor.get("locations") or []
        for loc in locs:
            if loc.get("is_primary"):
                v_state = _norm_state_code(loc.get("state_province")) or v_state
                v_country = _norm_country(loc.get("country_iso2")) or v_country
                break
        if v_state is None and locs:
            v_state = _norm_state_code(locs[0].get("state_province"))
            v_country = _norm_country(locs[0].get("country_iso2")) or v_country

        # Tiers
        if geo_ctx.country_iso2 and v_country and v_country == geo_ctx.country_iso2:
            if geo_ctx.local_state and v_state == geo_ctx.local_state:
                return "local"
            if geo_ctx.regional_states and v_state in geo_ctx.regional_states:
                return "regional"
            return "national"
        # Cross-border
        return "global"

    def bucket_vendors_by_geo_tier(
        self,
        vendors: Iterable[dict[str, Any]],
        geo_ctx: GeoContext,
    ) -> GeoBuckets:
        buckets = GeoBuckets()
        for vendor in vendors:
            tier = self._classify_one(vendor, geo_ctx)
            vendor_copy = dict(vendor)
            vendor_copy["geo_tier"] = tier
            if tier == "local":
                buckets.local.append(vendor_copy)
            elif tier == "regional":
                buckets.regional.append(vendor_copy)
            elif tier == "national":
                buckets.national.append(vendor_copy)
            else:
                # Global tier requires export_capable
                if vendor_copy.get("export_capable") is False:
                    # Demote to national-ish if we had a country match, else skip
                    logger.debug(
                        "skipping non-export-capable cross-border vendor id=%s",
                        vendor_copy.get("id"),
                    )
                    continue
                buckets.global_.append(vendor_copy)
        return buckets

    # ── Logistics profiles ────────────────────────────────────────────────

    def _lookup_freight_rate_usd_per_kg(
        self,
        db: Session,
        destination_region: str | None,
    ) -> Decimal | None:
        if not destination_region:
            return None
        hit = (
            db.query(FreightRate)
            .filter(FreightRate.destination_region.ilike(f"%{destination_region}%"))
            .order_by(FreightRate.effective_from.desc())
            .first()
        )
        if hit and hit.rate_per_kg is not None:
            return Decimal(str(hit.rate_per_kg))
        return None

    def compute_logistics_profile(
        self,
        vendor: dict[str, Any],
        geo_ctx: GeoContext,
        db: Session | None = None,
        assumed_weight_kg: Decimal | None = None,
    ) -> LogisticsProfile:
        """Estimate shipping mode, transit time, and freight cost per tier."""
        tier = vendor.get("geo_tier") or self._classify_one(vendor, geo_ctx)
        weight = Decimal(str(assumed_weight_kg)) if assumed_weight_kg else Decimal("10")

        # Baseline per-kg cost bands (USD). Overridden by FreightRate when present.
        tier_defaults = {
            "local":    ("truck",           1,  3, Decimal("0.30"), 0.80),
            "regional": ("truck_or_rail",   3,  7, Decimal("0.60"), 0.70),
            "national": ("rail_or_road",    5, 15, Decimal("1.20"), 0.60),
            "global":   ("sea_or_air",     15, 45, Decimal("4.50"), 0.50),
        }
        mode, tmin, tmax, per_kg_default, confidence = tier_defaults.get(
            tier, tier_defaults["national"]
        )

        rate_override: Decimal | None = None
        if db is not None:
            region_probe = (
                geo_ctx.state_province
                or geo_ctx.country_iso2
                or vendor.get("country")
            )
            rate_override = self._lookup_freight_rate_usd_per_kg(db, region_probe)

        per_kg = rate_override if rate_override is not None else per_kg_default
        est_cost = (per_kg * weight).quantize(Decimal("0.01"))

        return LogisticsProfile(
            shipping_mode=mode,
            est_transit_days_min=tmin,
            est_transit_days_max=tmax,
            est_freight_cost_usd=est_cost,
            confidence=confidence if rate_override is None else min(1.0, confidence + 0.20),
            geo_tier=tier,
        )


geo_tier_service = GeoTierService()
