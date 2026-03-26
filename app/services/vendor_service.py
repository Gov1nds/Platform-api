"""Vendor Service — FIXED: N+1 query, vendor seeding."""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.vendor import Vendor
from app.models.memory import SupplierMemory

logger = logging.getLogger("vendor_service")

# Flag to avoid re-seeding on every call
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
    # NEW: External API vendor placeholder for attributed pricing
    {"name": "External API", "country": "Global", "region": "Global",
     "capabilities": [], "rating": 3.0, "avg_lead_time": 14},
]


def seed_vendors(db: Session):
    """Seed database with initial vendors if empty. FIXED: uses flag to avoid per-request check."""
    global _vendors_seeded
    if _vendors_seeded:
        return
    if db.query(Vendor).count() > 0:
        _vendors_seeded = True
        return
    for v in SEED_VENDORS:
        vendor = Vendor(
            name=v["name"], country=v["country"], region=v["region"],
            capabilities=v["capabilities"], rating=v["rating"],
            reliability_score=v["rating"] / 5.0, avg_lead_time=v["avg_lead_time"],
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
    """FIXED: Single joined query instead of N+1."""
    results = (
        db.query(SupplierMemory, Vendor)
        .join(Vendor, SupplierMemory.vendor_id == Vendor.id)
        .filter(Vendor.is_active == True)
        .all()
    )
    return {
        vendor.region: {
            "vendor_id": vendor.id,
            "total_orders": mem.total_orders,
            "cost_accuracy_score": mem.cost_accuracy_score,
            "delivery_accuracy_score": mem.delivery_accuracy_score,
            "performance_score": mem.performance_score,
            "risk_level": mem.risk_level,
        }
        for mem, vendor in results
    }