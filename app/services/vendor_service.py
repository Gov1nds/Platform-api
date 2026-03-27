"""Vendor Service — updated for pricing.vendors PostgreSQL schema."""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.vendor import Vendor
from app.models.memory import SupplierMemory

logger = logging.getLogger("vendor_service")
_vendors_seeded = False

SEED_VENDORS = [
    {"name": "PGI India", "country": "India", "region": "India",
     "capabilities": ["CNC", "sheet_metal", "fasteners", "assembly"], "rating": 4.2, "avg_lead_time": 18},
    {"name": "PGI China", "country": "China", "region": "China",
     "capabilities": ["injection_molding", "die_casting", "PCB", "electronics"], "rating": 4.0, "avg_lead_time": 22},
    {"name": "PGI Vietnam", "country": "Vietnam", "region": "Vietnam",
     "capabilities": ["assembly", "wiring", "machining"], "rating": 3.8, "avg_lead_time": 24},
    {"name": "PGI Mexico", "country": "Mexico", "region": "Mexico",
     "capabilities": ["automotive", "sheet_metal", "assembly"], "rating": 3.9, "avg_lead_time": 12},
    {"name": "PGI EU", "country": "Germany", "region": "EU (Germany)",
     "capabilities": ["precision_CNC", "5-axis", "medical", "automotive"], "rating": 4.8, "avg_lead_time": 10},
    {"name": "Local Workshop", "country": "Local", "region": "Local",
     "capabilities": ["prototyping", "CNC", "sheet_metal"], "rating": 3.5, "avg_lead_time": 7},
    {"name": "External API", "country": "Global", "region": "Global",
     "capabilities": [], "rating": 3.0, "avg_lead_time": 14},
]


def seed_vendors(db: Session):
    global _vendors_seeded
    if _vendors_seeded:
        return
    if db.query(Vendor).count() > 0:
        _vendors_seeded = True
        return
    for v in SEED_VENDORS:
        vendor = Vendor(
            name=v["name"],
            reliability_score=(v["rating"] / 5.0),
            avg_lead_time_days=v["avg_lead_time"],
            is_active=True,
            metadata_={
                "country_name": v["country"],
                "region_name": v["region"],
                "capabilities": v["capabilities"],
            },
        )
        db.add(vendor)
        db.flush()
        db.add(SupplierMemory(vendor_id=vendor.id))
    db.commit()
    _vendors_seeded = True
    logger.info(f"Seeded {len(SEED_VENDORS)} vendors")


def get_all_vendors(db: Session) -> List[Vendor]:
    return db.query(Vendor).filter(Vendor.is_active == True).all()


def get_vendor(db: Session, vendor_id: str) -> Optional[Vendor]:
    return db.query(Vendor).filter(Vendor.id == vendor_id).first()


def get_vendor_memories(db: Session) -> Dict[str, Dict]:
    results = (
        db.query(SupplierMemory, Vendor)
        .join(Vendor, SupplierMemory.vendor_id == Vendor.id)
        .filter(Vendor.is_active == True)
        .all()
    )
    return {
        vendor.region: {
            "vendor_id": vendor.id,
            "total_orders": int(mem.total_orders or 0),
            "cost_accuracy_score": float(mem.cost_accuracy_score or 0.5),
            "delivery_accuracy_score": float(mem.delivery_accuracy_score or 0.5),
            "performance_score": float(mem.performance_score or 0.5),
            "risk_level": mem.risk_level or "medium",
        }
        for mem, vendor in results
    }
