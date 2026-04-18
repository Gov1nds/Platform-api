"""
ARQ-based async worker — live implementations (Blueprint §23.1, §24).
Usage: python -m arq app.workers.arq_worker.WorkerSettings
"""
from __future__ import annotations
import asyncio, json, logging, time
from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

try:
    from arq import cron
except ImportError:
    def cron(fn, **kw): return fn

# ── 1. Forex refresh (every 15 min) ─────────────────────────────────────────
async def refresh_forex_rates(ctx: dict) -> dict:
    from app.integrations.open_exchange_rates import OpenExchangeRatesClient
    from app.models.market import FXRate
    from app.services.freshness_service import log_refresh
    client = OpenExchangeRatesClient()
    if not client.configured:
        return {"updated": 0, "errors": 1, "skip": "missing OPEN_EXCHANGE_RATES_APP_ID"}
    t0 = time.time(); now = datetime.now(timezone.utc)
    try:
        data = await client.latest("USD")
    except Exception as e:
        logger.exception("OXR fetch failed")
        with SessionLocal() as db:
            log_refresh(db, table_name="fx_rates", record_id="USD",
                source_api="openexchangerates", status="error",
                error_message=str(e), duration_ms=int((time.time()-t0)*1000))
            db.commit()
        return {"updated": 0, "errors": 1}
    rates = data.get("rates", {}); updated = 0
    with SessionLocal() as db:
        for cur in ("EUR","CNY","INR","VND","MXN","GBP","JPY","AUD","SGD","CAD",
                    "CHF","HKD","KRW","BRL","ZAR","TRY","SEK","NOK","PLN","THB"):
            val = rates.get(cur)
            if not val: continue
            row = db.query(FXRate).filter_by(from_currency="USD", to_currency=cur).first()
            prev = float(row.rate) if row and row.rate else None
            if not row:
                row = FXRate(from_currency="USD", to_currency=cur)
                db.add(row)
            row.rate = val; row.fetched_at = now
            row.valid_until = now + timedelta(minutes=60)
            row.source = "openexchangerates"; row.freshness_status = "FRESH"
            updated += 1
            log_refresh(db, table_name="fx_rates", record_id=f"USD/{cur}",
                source_api="openexchangerates", status="success",
                previous_value_json={"rate": prev} if prev else None,
                new_value_json={"rate": val},
                duration_ms=int((time.time()-t0)*1000))
        db.commit()
    return {"updated": updated, "errors": 0}

# ── 2. Baseline price refresh (nightly) ─────────────────────────────────────
async def refresh_baseline_prices(ctx: dict) -> dict:
    from app.integrations.distributor_connector import DistributorAggregator
    from app.services.freshness_service import log_refresh
    from sqlalchemy import text as sa_text
    aggregator = DistributorAggregator()
    if not any(c.configured for c in aggregator.clients):
        return {"updated": 0, "skip": "no distributor keys"}
    t0 = time.time(); updated = errors = 0
    with SessionLocal() as db:
        rows = db.execute(sa_text(
            "SELECT part_id, manufacturer_part_number FROM part_master "
            "WHERE manufacturer_part_number IS NOT NULL LIMIT 2000")).fetchall()
        for r in rows:
            try:
                offers = await aggregator.search(r.manufacturer_part_number)
                band = DistributorAggregator.to_price_band(offers)
                if not band: continue
                db.execute(sa_text("""
                    INSERT INTO baseline_price (part_id, quantity_break, price_floor, price_mid,
                        price_ceiling, currency, source_type, data_source_name, sources_json,
                        fetched_at, valid_until, freshness_status)
                    VALUES (:pid, 1, :floor, :mid, :ceiling, :cur, 'distributor', 'aggregate',
                        :sources::jsonb, NOW(), NOW() + INTERVAL '24 hours', 'FRESH')
                    ON CONFLICT (part_id, quantity_break) DO UPDATE SET
                        price_floor=EXCLUDED.price_floor, price_mid=EXCLUDED.price_mid,
                        price_ceiling=EXCLUDED.price_ceiling, fetched_at=EXCLUDED.fetched_at,
                        valid_until=EXCLUDED.valid_until, freshness_status='FRESH'
                """), {"pid": r.part_id, "floor": float(band["floor"]), "mid": float(band["mid"]),
                       "ceiling": float(band["ceiling"]), "cur": band["currency"],
                       "sources": json.dumps(list({o.get("source","") for o in offers}))})
                updated += 1
            except Exception as e:
                errors += 1
        db.commit()
    return {"updated": updated, "errors": errors}

# ── 3. Tariff refresh (weekly) ───────────────────────────────────────────────
async def refresh_tariff_rates(ctx: dict) -> dict:
    from app.integrations.tariff_client import TariffClient
    from app.services.freshness_service import log_refresh
    from sqlalchemy import text as sa_text
    client = TariffClient(); rows = []; errors = 0
    try: rows.extend(await client.load_cbp_usa())
    except: errors += 1
    try: rows.extend(await client.load_fta_routes())
    except: errors += 1
    updated = 0
    with SessionLocal() as db:
        for r in rows:
            try:
                db.execute(sa_text("""
                    INSERT INTO tariff_schedules (hs_code, from_country, to_country, duty_rate_pct,
                        fta_eligible, fta_agreement_name, effective_date, fetched_at, freshness_status)
                    VALUES (:hs, :fc, :tc, :rate, COALESCE(:fta, FALSE), :fta_name,
                            :eff::date, NOW(), 'FRESH')
                    ON CONFLICT (hs_code, from_country, to_country) DO UPDATE SET
                        duty_rate_pct=EXCLUDED.duty_rate_pct, fta_eligible=EXCLUDED.fta_eligible,
                        freshness_status='FRESH', fetched_at=NOW()
                """), {"hs": r.get("hs_code",""), "fc": r.get("from_country","ANY"),
                       "tc": r.get("to_country","US"), "rate": r.get("duty_rate_pct",0),
                       "fta": r.get("fta_eligible"), "fta_name": r.get("fta_agreement_name"),
                       "eff": r.get("effective_date", datetime.now(timezone.utc).date().isoformat())})
                updated += 1
            except: errors += 1
        log_refresh(db, table_name="tariff_schedules", record_id="batch",
            source_api="cbp+fta", status="success" if errors==0 else "stale",
            new_value_json={"rows": updated, "errors": errors})
        db.commit()
    return {"updated": updated, "errors": errors}

# ── 4. Logistics rate refresh (daily) ────────────────────────────────────────
async def refresh_logistics_rates(ctx: dict) -> dict:
    from app.integrations.dhl_client import DHLClient
    from app.integrations.fedex_client import FedExClient
    from app.integrations.ups_client import UPSClient
    from app.services.freshness_service import log_refresh
    from sqlalchemy import text as sa_text
    carriers = [c for c in (DHLClient(), FedExClient(), UPSClient()) if c.configured]
    if not carriers:
        return {"updated": 0, "skip": "no carrier keys"}
    LANES = [("IN","US"),("IN","DE"),("CN","US"),("CN","DE"),("VN","US"),("MX","US")]
    BANDS = [(0.5,"0-500g"),(5,"500g-5kg"),(25,"5-25kg"),(100,"25-100kg")]
    updated = errors = 0
    with SessionLocal() as db:
        for (og, ds) in LANES:
            for (wt, band) in BANDS:
                for carrier in carriers:
                    try:
                        q = await carrier.rate(origin=og, destination=ds, weight_kg=wt)
                        db.execute(sa_text("""
                            INSERT INTO logistics_rate (origin_country, destination_country, carrier,
                                service_level, weight_band, cost_estimate, currency,
                                transit_days_min, transit_days_max, fetched_at, valid_until, freshness_status)
                            VALUES (:og, :ds, :c, :sv, :b, :cost, :cur, :tmin, :tmax,
                                    NOW(), NOW() + INTERVAL '48 hours', 'FRESH')
                            ON CONFLICT (origin_country, destination_country, carrier, service_level, weight_band)
                            DO UPDATE SET cost_estimate=EXCLUDED.cost_estimate,
                                transit_days_min=EXCLUDED.transit_days_min,
                                transit_days_max=EXCLUDED.transit_days_max,
                                fetched_at=NOW(), freshness_status='FRESH'
                        """), {"og": og, "ds": ds, "c": carrier.name, "sv": q["service_level"],
                               "b": band, "cost": float(q["cost"]), "cur": q.get("currency","USD"),
                               "tmin": q["transit_days_min"], "tmax": q["transit_days_max"]})
                        updated += 1
                    except Exception as e:
                        errors += 1
        db.commit()
    return {"updated": updated, "errors": errors}

# ── 5. Vendor snapshot rebuild (nightly 02:00) ───────────────────────────────
async def rebuild_vendor_snapshots(ctx: dict) -> dict:
    from app.services.report_service import report_service
    with SessionLocal() as db:
        count = report_service.rebuild_vendor_performance_snapshots(db)
        db.commit()
    return {"snapshots_rebuilt": count}

# ── 6. AI insight generation (nightly 03:00) ─────────────────────────────────
async def generate_ai_insights(ctx: dict) -> dict:
    from app.services.report_service import report_service
    generated = 0
    with SessionLocal() as db:
        try:
            from app.models.user import Organization
            for org in db.query(Organization).all():
                summary = report_service.generate_insight_summary(db, org_id=str(org.id))
                from app.models.report_snapshot_v2 import ReportSnapshotV2
                db.add(ReportSnapshotV2(organization_id=org.id, report_type="insight_summary",
                    ai_insight_text=summary, payload_json={}))
                generated += 1
            db.commit()
        except Exception:
            logger.exception("AI insight generation failed")
    return {"insights_generated": generated}

# ── 7. SLA monitor (every 10 min) ────────────────────────────────────────────
async def run_sla_monitor(ctx: dict) -> dict:
    from app.workers.tasks.sla_monitor import check_sla_breaches
    with SessionLocal() as db:
        breaches = check_sla_breaches(db)
        db.commit()
    return {"breaches": breaches}

# ── 8. Part_Master embedding rebuild (weekly) ────────────────────────────────
async def rebuild_part_master_index(ctx: dict) -> dict:
    import httpx
    from sqlalchemy import text as sa_text
    total = 0
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            with SessionLocal() as db:
                rows = db.execute(sa_text(
                    "SELECT part_id, canonical_name, spec_template FROM part_master "
                    "WHERE embedding IS NULL LIMIT 5000")).fetchall()
                for batch in [rows[i:i+32] for i in range(0, len(rows), 32)]:
                    texts = [f"{r.canonical_name} | {json.dumps(r.spec_template or {})}" for r in batch]
                    r = await c.post(f"{settings.BOM_ANALYZER_URL}/api/embed",
                        json={"texts": texts},
                        headers={"X-Internal-Key": settings.INTERNAL_API_KEY})
                    r.raise_for_status()
                    vectors = r.json()["vectors"]
                    for row, vec in zip(batch, vectors):
                        db.execute(sa_text("UPDATE part_master SET embedding = :v WHERE part_id = :id"),
                            {"v": vec, "id": row.part_id})
                    total += len(batch)
                db.commit()
    except Exception:
        logger.exception("Part_Master embedding rebuild failed")
    return {"embedded": total}

# ── 9. GDPR pending deletions (daily 04:00) ─────────────────────────────────
async def execute_pending_deletions(ctx: dict) -> dict:
    from app.services.compliance.deletion_service import execute_deletions
    with SessionLocal() as db:
        count = execute_deletions(db)
        db.commit()
    return {"deleted": count}

# ── WorkerSettings ───────────────────────────────────────────────────────────
class WorkerSettings:
    functions = [
        refresh_forex_rates, refresh_baseline_prices, refresh_tariff_rates,
        refresh_logistics_rates, rebuild_vendor_snapshots, generate_ai_insights,
        run_sla_monitor, rebuild_part_master_index, execute_pending_deletions,
    ]
    cron_jobs = [
        cron(refresh_forex_rates, minute={0, 15, 30, 45}),
        cron(refresh_baseline_prices, hour=1, minute=30),
        cron(refresh_tariff_rates, weekday="sun", hour=4, minute=0),
        cron(refresh_logistics_rates, hour=0, minute=30),
        cron(rebuild_vendor_snapshots, hour=2, minute=0),
        cron(generate_ai_insights, hour=3, minute=0),
        cron(run_sla_monitor, minute={0, 10, 20, 30, 40, 50}),
        cron(rebuild_part_master_index, weekday="sat", hour=5, minute=0),
        cron(execute_pending_deletions, hour=4, minute=0),
    ]
    redis_settings = None

    @staticmethod
    async def startup(ctx):
        logger.info("Arq worker starting")

    @staticmethod
    async def shutdown(ctx):
        logger.info("Arq worker shutting down")
