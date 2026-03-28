"""
Geo Service — Seed and query region profiles, exchange rates, tariffs from DB.

On startup, seeds from hardcoded defaults if tables are empty.
At runtime, strategy_service queries this instead of hardcoded dicts.
Falls back to hardcoded data if DB query fails.
"""
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.geo import RegionProfile, ExchangeRate, TariffRule

logger = logging.getLogger("geo_service")

_seeded = False

# ── Seed data (same as old REGION_PROFILES / FOREX_RATES) ──

_SEED_REGIONS = {
    "India": {
        "base_cost_mult": 0.35, "labor_rate_hr": 12, "lead_days_base": 18,
        "logistics_per_kg": 3.5, "tariff_pct": 0.05, "risk_base": 0.15,
        "quality_score": 0.78, "moq_threshold": 50,
        "distance_km": {"India": 500, "USA": 14000, "EU (Germany)": 7000, "China": 5000, "Local": 500},
        "process_fit": {"sheet_metal": 0.95, "CNC": 0.85, "fasteners": 0.95, "welding": 0.90,
                        "injection_molding": 0.65, "die_casting": 0.60, "PCB": 0.70, "electronics": 0.75},
        "material_fit": {"stainless_steel": 0.90, "carbon_steel": 0.95, "aluminum": 0.85, "plastic": 0.65},
        "capabilities": ["CNC", "sheet_metal", "fasteners", "welding", "assembly", "electronics"],
    },
    "China": {
        "base_cost_mult": 0.40, "labor_rate_hr": 15, "lead_days_base": 22,
        "logistics_per_kg": 3.0, "tariff_pct": 0.08, "risk_base": 0.18,
        "quality_score": 0.82, "moq_threshold": 100,
        "distance_km": {"India": 5000, "USA": 12000, "EU (Germany)": 8000, "China": 300, "Local": 12000},
        "process_fit": {"injection_molding": 0.98, "die_casting": 0.95, "PCB": 0.95, "electronics": 0.92,
                        "stamping": 0.90, "CNC": 0.80, "sheet_metal": 0.82, "fasteners": 0.85},
        "material_fit": {"plastic": 0.95, "aluminum": 0.85, "stainless_steel": 0.80, "carbon_steel": 0.85},
        "capabilities": ["injection_molding", "die_casting", "PCB", "electronics", "stamping", "CNC", "sheet_metal"],
    },
    "Vietnam": {
        "base_cost_mult": 0.38, "labor_rate_hr": 10, "lead_days_base": 24,
        "logistics_per_kg": 4.0, "tariff_pct": 0.04, "risk_base": 0.20,
        "quality_score": 0.72, "moq_threshold": 100,
        "distance_km": {"India": 4000, "USA": 14000, "EU (Germany)": 9000, "China": 2000, "Local": 14000},
        "process_fit": {"assembly": 0.90, "wiring": 0.88, "CNC": 0.60, "sheet_metal": 0.65, "electronics": 0.72},
        "material_fit": {"plastic": 0.70, "carbon_steel": 0.65, "aluminum": 0.60},
        "capabilities": ["assembly", "wiring", "CNC", "electronics"],
    },
    "Mexico": {
        "base_cost_mult": 0.55, "labor_rate_hr": 18, "lead_days_base": 12,
        "logistics_per_kg": 2.0, "tariff_pct": 0.02, "risk_base": 0.12,
        "quality_score": 0.80, "moq_threshold": 25,
        "distance_km": {"India": 16000, "USA": 2000, "EU (Germany)": 9000, "China": 12000, "Local": 2000},
        "process_fit": {"sheet_metal": 0.85, "stamping": 0.88, "assembly": 0.82, "CNC": 0.75},
        "material_fit": {"carbon_steel": 0.85, "stainless_steel": 0.78, "aluminum": 0.80},
        "capabilities": ["sheet_metal", "stamping", "assembly", "CNC"],
    },
    "EU (Germany)": {
        "base_cost_mult": 0.90, "labor_rate_hr": 55, "lead_days_base": 10,
        "logistics_per_kg": 1.5, "tariff_pct": 0.03, "risk_base": 0.05,
        "quality_score": 0.95, "moq_threshold": 5,
        "distance_km": {"India": 7000, "USA": 8000, "EU (Germany)": 300, "China": 8000, "Local": 8000},
        "process_fit": {"CNC": 0.98, "precision_CNC": 1.0, "grinding": 0.95, "5_axis": 0.98,
                        "sheet_metal": 0.80, "injection_molding": 0.75},
        "material_fit": {"stainless_steel": 0.95, "aluminum": 0.95, "titanium": 0.90, "carbon_steel": 0.90},
        "capabilities": ["CNC", "precision_CNC", "grinding", "5_axis", "sheet_metal", "injection_molding"],
    },
    "USA": {
        "base_cost_mult": 1.00, "labor_rate_hr": 65, "lead_days_base": 8,
        "logistics_per_kg": 1.0, "tariff_pct": 0.00, "risk_base": 0.03,
        "quality_score": 0.93, "moq_threshold": 5,
        "distance_km": {"India": 14000, "USA": 300, "EU (Germany)": 8000, "China": 12000, "Local": 300},
        "process_fit": {"CNC": 0.95, "precision_CNC": 0.95, "3d_printing": 0.98, "PCB": 0.85,
                        "sheet_metal": 0.80, "electronics": 0.88},
        "material_fit": {"aluminum": 0.95, "stainless_steel": 0.90, "titanium": 0.92},
        "capabilities": ["CNC", "precision_CNC", "3d_printing", "PCB", "sheet_metal", "electronics"],
    },
    "Local": {
        "base_cost_mult": 0.95, "labor_rate_hr": 50, "lead_days_base": 7,
        "logistics_per_kg": 0.5, "tariff_pct": 0.00, "risk_base": 0.05,
        "quality_score": 0.88, "moq_threshold": 1,
        "distance_km": {"India": 500, "USA": 300, "EU (Germany)": 300, "China": 300, "Local": 50},
        "process_fit": {"CNC": 0.80, "sheet_metal": 0.80, "3d_printing": 0.85, "assembly": 0.75,
                        "fasteners": 0.70, "electronics": 0.70},
        "material_fit": {"aluminum": 0.80, "stainless_steel": 0.80, "carbon_steel": 0.80, "plastic": 0.75},
        "capabilities": ["CNC", "sheet_metal", "3d_printing", "assembly"],
    },
}

_SEED_FOREX = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.5, "CNY": 7.25,
    "JPY": 155.0, "KRW": 1350.0, "MXN": 17.2, "THB": 36.0, "VND": 24800.0,
    "TWD": 31.5, "CAD": 1.37, "AUD": 1.55,
}


def seed_geo_data(db: Session):
    """Seed region profiles and exchange rates if tables are empty."""
    global _seeded
    if _seeded:
        return

    try:
        if db.query(RegionProfile).count() == 0:
            for name, data in _SEED_REGIONS.items():
                db.add(RegionProfile(
                    region_name=name,
                    base_cost_mult=data["base_cost_mult"],
                    labor_rate_hr=data["labor_rate_hr"],
                    lead_days_base=data["lead_days_base"],
                    logistics_per_kg=data["logistics_per_kg"],
                    tariff_pct=data["tariff_pct"],
                    risk_base=data["risk_base"],
                    quality_score=data["quality_score"],
                    moq_threshold=data["moq_threshold"],
                    distance_km=data["distance_km"],
                    process_fit=data["process_fit"],
                    material_fit=data["material_fit"],
                    capabilities=data["capabilities"],
                ))
            logger.info(f"Seeded {len(_SEED_REGIONS)} region profiles")

        if db.query(ExchangeRate).count() == 0:
            for currency, rate in _SEED_FOREX.items():
                db.add(ExchangeRate(
                    from_currency=currency,
                    to_currency="USD",
                    rate=rate,
                    source="seed",
                    is_current=True,
                ))
            logger.info(f"Seeded {len(_SEED_FOREX)} exchange rates")

        db.commit()
        _seeded = True
    except Exception as e:
        logger.warning(f"Geo seeding failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def get_region_profiles(db: Session) -> Dict[str, Dict[str, Any]]:
    """Get region profiles from DB. Falls back to hardcoded data."""
    try:
        profiles = db.query(RegionProfile).filter(RegionProfile.is_active == True).all()
        if profiles:
            result = {}
            for p in profiles:
                result[p.region_name] = {
                    "base_cost_mult": float(p.base_cost_mult),
                    "labor_rate_hr": float(p.labor_rate_hr),
                    "lead_days_base": int(p.lead_days_base),
                    "logistics_per_kg": float(p.logistics_per_kg),
                    "tariff_pct": float(p.tariff_pct),
                    "risk_base": float(p.risk_base),
                    "quality_score": float(p.quality_score),
                    "moq_threshold": int(p.moq_threshold),
                    "distance_km": p.distance_km or {},
                    "process_fit": p.process_fit or {},
                    "material_fit": p.material_fit or {},
                    "capabilities": p.capabilities or [],
                }
            return result
    except Exception as e:
        logger.debug(f"DB region profiles unavailable: {e}")

    # Fallback to hardcoded
    return _SEED_REGIONS


def get_forex_rates(db: Session) -> Dict[str, float]:
    """Get exchange rates from DB. Falls back to hardcoded data."""
    try:
        rates = db.query(ExchangeRate).filter(ExchangeRate.is_current == True).all()
        if rates:
            return {r.from_currency: float(r.rate) for r in rates}
    except Exception as e:
        logger.debug(f"DB forex rates unavailable: {e}")

    return _SEED_FOREX
