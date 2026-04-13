from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import CommodityIndex, FXRate, FreightRate
from app.seeds.base import SeedStats, load_records, parse_datetime, parse_decimal

logger = logging.getLogger(__name__)


def load_market(seed_root, db: Session) -> list[SeedStats]:
    return [
        _load_fx_rates(seed_root, db),
        _load_freight_rates(seed_root, db),
        _load_commodity_indices(seed_root, db),
    ]


def _load_fx_rates(seed_root, db: Session) -> SeedStats:
    stats = SeedStats(name="market.fx_rates")
    for row in load_records(seed_root, "market/fx_rates_baseline.json"):
        existing = db.execute(
            select(FXRate).where(
                FXRate.base_currency == row["base_currency"],
                FXRate.quote_currency == row["quote_currency"],
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = FXRate(
                base_currency=row["base_currency"],
                quote_currency=row["quote_currency"],
            )
            db.add(existing)
            stats.inserted += 1
        else:
            stats.updated += 1

        existing.rate = parse_decimal(row["rate"])
        existing.source = row.get("source")
        existing.confidence = parse_decimal(row.get("confidence")) or 1
        existing.freshness_status = row.get("freshness_status") or "STALE"
        existing.ttl_seconds = int(row.get("ttl_seconds") or 900)
        existing.fetched_at = parse_datetime(row.get("fetched_at"))
        existing.effective_from = parse_datetime(row.get("created_at")) or existing.effective_from
        existing.effective_to = parse_datetime(row.get("expires_at"))
        existing.last_verified_at = parse_datetime(row.get("updated_at")) or existing.last_verified_at
        existing.provider_id = "phase1_seed_assets"
        existing.data_source = "baseline_seed"

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _load_freight_rates(seed_root, db: Session) -> SeedStats:
    stats = SeedStats(name="market.freight_rates")
    for row in load_records(seed_root, "market/freight_rates_baseline.json"):
        existing = db.execute(
            select(FreightRate).where(
                FreightRate.origin_region == row["origin_region"],
                FreightRate.destination_region == row["destination_region"],
                FreightRate.mode == row["mode"],
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = FreightRate(
                origin_region=row["origin_region"],
                destination_region=row["destination_region"],
                mode=row["mode"],
            )
            db.add(existing)
            stats.inserted += 1
        else:
            stats.updated += 1

        existing.rate_per_kg = parse_decimal(row.get("rate_per_kg"))
        existing.rate_per_cbm = parse_decimal(row.get("rate_per_cbm"))
        existing.min_charge = parse_decimal(row.get("min_charge"))
        existing.currency = row.get("currency") or "USD"
        existing.transit_days = parse_decimal(row.get("transit_days"))
        existing.source = row.get("source")
        existing.confidence = parse_decimal(row.get("confidence")) or 1
        existing.freshness_status = row.get("freshness_status") or "STALE"
        existing.ttl_seconds = int(row.get("ttl_seconds") or 3600)
        existing.fetched_at = parse_datetime(row.get("fetched_at"))
        existing.effective_from = parse_datetime(row.get("created_at")) or existing.effective_from
        existing.effective_to = parse_datetime(row.get("expires_at"))
        existing.provider_id = "phase1_seed_assets"
        existing.data_source = "baseline_seed"

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats


def _load_commodity_indices(seed_root, db: Session) -> SeedStats:
    stats = SeedStats(name="market.commodity_indices")
    for row in load_records(seed_root, "market/commodity_index_baseline.json"):
        existing = db.execute(
            select(CommodityIndex).where(
                CommodityIndex.commodity_name == row["commodity_name"],
                CommodityIndex.unit == row["unit"],
                CommodityIndex.currency == row.get("currency", "USD"),
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = CommodityIndex(
                commodity_name=row["commodity_name"],
                unit=row["unit"],
                currency=row.get("currency") or "USD",
            )
            db.add(existing)
            stats.inserted += 1
        else:
            stats.updated += 1

        existing.price = parse_decimal(row["price"])
        existing.source = row.get("source")
        existing.confidence = parse_decimal(row.get("confidence")) or 1
        existing.freshness_status = row.get("freshness_status") or "STALE"
        existing.ttl_seconds = int(row.get("ttl_seconds") or 3600)
        existing.fetched_at = parse_datetime(row.get("fetched_at"))
        existing.effective_from = parse_datetime(row.get("created_at")) or existing.effective_from
        existing.effective_to = parse_datetime(row.get("expires_at"))
        existing.provider_id = "phase1_seed_assets"
        existing.data_source = "baseline_seed"

    logger.info("seeded %s | inserted=%s updated=%s", stats.name, stats.inserted, stats.updated)
    return stats
