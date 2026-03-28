"""Vendor Service — updated for pricing.vendors + pricing.vendor_capabilities PostgreSQL schema."""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.vendor import Vendor, VendorCapability
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
        # Backfill capabilities if table is empty but vendors exist
        if db.query(VendorCapability).count() == 0:
            _seed_capabilities(db)
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
        # Seed capability entries
        for cap in v["capabilities"]:
            db.add(VendorCapability(
                vendor_id=vendor.id,
                process=cap,
                proficiency=0.85,
                typical_lead_days=v["avg_lead_time"],
            ))
    db.commit()
    _vendors_seeded = True
    logger.info(f"Seeded {len(SEED_VENDORS)} vendors with capabilities")


def _seed_capabilities(db: Session):
    """Backfill vendor_capabilities from metadata_ for existing vendors."""
    vendors = db.query(Vendor).all()
    count = 0
    for v in vendors:
        caps = (v.metadata_ or {}).get("capabilities", [])
        for cap in caps:
            existing = db.query(VendorCapability).filter(
                VendorCapability.vendor_id == v.id,
                VendorCapability.process == cap,
            ).first()
            if not existing:
                db.add(VendorCapability(
                    vendor_id=v.id,
                    process=cap,
                    proficiency=0.80,
                    typical_lead_days=float(v.avg_lead_time_days or 14),
                ))
                count += 1
    if count:
        db.commit()
        logger.info(f"Backfilled {count} vendor capabilities")


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


def get_vendors_for_process(db: Session, process: str, material_family: str = None) -> List[Dict[str, Any]]:
    """Query vendor_capabilities table to find vendors that can handle a process.
    Returns list of {vendor_id, vendor_name, region, proficiency, lead_days}."""
    from sqlalchemy import desc
    query = (
        db.query(VendorCapability, Vendor)
        .join(Vendor, VendorCapability.vendor_id == Vendor.id)
        .filter(
            VendorCapability.is_active == True,
            Vendor.is_active == True,
        )
    )

    # Match process — check both exact and substring
    query = query.filter(
        VendorCapability.process.ilike(f"%{process}%")
    )

    if material_family:
        # If material specified, prefer vendors with that material capability
        # but don't exclude others
        pass

    results = query.order_by(desc(VendorCapability.proficiency)).limit(10).all()

    return [
        {
            "vendor_id": str(vendor.id),
            "vendor_name": vendor.name,
            "region": vendor.region,
            "process": cap.process,
            "proficiency": float(cap.proficiency or 0.8),
            "typical_lead_days": float(cap.typical_lead_days or 14),
            "certifications": cap.certifications or [],
        }
        for cap, vendor in results
    ]


def get_vendor_capability_map(db: Session) -> Dict[str, List[str]]:
    """Get {region: [process1, process2, ...]} from vendor_capabilities table.
    Used by strategy_service for capability matching against DB data."""
    results = (
        db.query(VendorCapability.process, Vendor)
        .join(Vendor, VendorCapability.vendor_id == Vendor.id)
        .filter(VendorCapability.is_active == True, Vendor.is_active == True)
        .all()
    )

    region_caps: Dict[str, set] = {}
    for cap_process, vendor in results:
        region = vendor.region
        if region not in region_caps:
            region_caps[region] = set()
        region_caps[region].add(cap_process)

    return {r: list(caps) for r, caps in region_caps.items()}
