"""Vendor Service — updated for pricing.vendors + pricing.vendor_capabilities PostgreSQL schema."""
import logging
from collections import defaultdict
from math import log1p
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import func, or_, desc
from sqlalchemy.orm import Session, joinedload

from app.models.vendor import Vendor, VendorCapability
from app.models.memory import SupplierMemory
from app.models.pricing import PricingQuote
from app.models.geo import RegionProfile, TariffRule
from app.models.vendor_match import VendorMatchRun, VendorMatch

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

def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _vendor_certifications(vendor: Vendor) -> List[str]:
    certs = set((vendor.metadata_ or {}).get("certifications", []) or [])
    for cap in vendor.capability_entries or []:
        for cert in (cap.certifications or []):
            certs.add(str(cert))
    return sorted(certs)


def _vendor_capability_tokens(vendor: Vendor) -> List[str]:
    tokens = set(vendor.capabilities or [])
    for cap in vendor.capability_entries or []:
        if cap.process:
            tokens.add(cap.process)
        if cap.material_family:
            tokens.add(cap.material_family)
    return sorted(tokens)


def _vendor_memory_snapshot(db: Session, vendor: Vendor) -> Dict[str, Any]:
    mem = vendor.memory
    if not mem:
        mem = db.query(SupplierMemory).filter(SupplierMemory.vendor_id == vendor.id).first()

    if not mem:
        return {
            "vendor_id": vendor.id,
            "total_orders": 0,
            "performance_score": 0.5,
            "cost_accuracy_score": 0.5,
            "delivery_accuracy_score": 0.5,
            "risk_level": "medium",
        }

    return {
        "vendor_id": mem.vendor_id,
        "total_orders": int(mem.total_orders or 0),
        "performance_score": _safe_float(mem.performance_score, 0.5),
        "cost_accuracy_score": _safe_float(mem.cost_accuracy_score, 0.5),
        "delivery_accuracy_score": _safe_float(mem.delivery_accuracy_score, 0.5),
        "risk_level": mem.risk_level or "medium",
        "avg_cost_delta_pct": _safe_float(mem.avg_cost_delta_pct, 0.0),
        "avg_lead_delta_days": _safe_float(mem.avg_lead_delta_days, 0.0),
    }


def _extract_project_parts(project) -> List[Dict[str, Any]]:
    report = project.analyzer_report or {}
    parts = report.get("section_2_component_breakdown", []) or []
    if not parts:
        parts = (project.project_metadata or {}).get("part_decisions", []) or []
    extracted = []
    for item in parts:
        extracted.append({
            "part_name": item.get("part_name") or item.get("description") or item.get("canonical_name") or "Part",
            "category": item.get("category") or "unknown",
            "process": item.get("process") or item.get("detected_process") or (item.get("selected_vendor") or {}).get("process_chain", [""])[0],
            "material": item.get("material") or item.get("detected_material") or "",
            "quantity": _safe_float(item.get("quantity"), 1.0) or 1.0,
            "best_region": item.get("best_region") or (item.get("selected_vendor") or {}).get("region") or project.recommended_location or "",
            "best_cost": _safe_float(item.get("best_cost"), 0.0),
            "lead_days": _safe_float(item.get("best_lead_days"), _safe_float(project.lead_time, 14.0)),
            "risk_score": _safe_float(item.get("risk_score"), _safe_float((item.get("risk") or {}).get("uncertainty"), 0.0)),
            "confidence_score": _safe_float(item.get("confidence_score"), _safe_float((item.get("risk") or {}).get("confidence"), 0.5)),
        })
    return extracted


def _score_overlap(a: str, b: str) -> float:
    a = _normalize_text(a)
    b = _normalize_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.82
    at = set(a.replace("_", " ").split())
    bt = set(b.replace("_", " ").split())
    if not at or not bt:
        return 0.2
    return min(1.0, len(at & bt) / max(len(at), len(bt)) + 0.2)


def _best_capability_for_part(part: Dict[str, Any], vendor: Vendor) -> Dict[str, Any]:
    best = None
    best_score = -1.0
    caps = vendor.capability_entries or []
    for cap in caps:
        process_fit = _score_overlap(part.get("process"), cap.process)
        material_fit = _score_overlap(part.get("material"), cap.material_family or "")
        score = process_fit * 0.7 + material_fit * 0.3 + _safe_float(cap.proficiency, 0.7) * 0.15
        if score > best_score:
            best_score = score
            best = cap
    return {
        "process": best.process if best else "",
        "material_family": best.material_family if best else "",
        "proficiency": _safe_float(best.proficiency, 0.7) if best else 0.7,
        "min_quantity": _safe_float(best.min_quantity, 0.0) if best else 0.0,
        "max_quantity": _safe_float(best.max_quantity, 0.0) if best else 0.0,
        "typical_lead_days": _safe_float(best.typical_lead_days, _safe_float(vendor.avg_lead_time_days, 14.0)) if best else _safe_float(vendor.avg_lead_time_days, 14.0),
        "certifications": list(best.certifications or []) if best else [],
        "notes": best.notes if best else "",
        "match_score": max(0.0, best_score),
    }


def _score_capacity(part_qty: float, cap: Dict[str, Any]) -> float:
    min_q = cap.get("min_quantity") or 0.0
    max_q = cap.get("max_quantity") or 0.0
    if max_q and part_qty > max_q:
        return 0.15
    if min_q and part_qty < min_q:
        return 0.55
    return 1.0


def _score_lead_time(target_lead: float, vendor_lead: float) -> float:
    target_lead = target_lead or 14.0
    vendor_lead = vendor_lead or target_lead
    gap = abs(vendor_lead - target_lead)
    return max(0.0, 1.0 - min(gap / max(target_lead * 1.5, 1.0), 1.0))


def _score_price(db: Session, vendor: Vendor, baseline_unit_price: float) -> Tuple[float, Dict[str, Any]]:
    quotes = (
        db.query(PricingQuote)
        .filter(PricingQuote.vendor_id == vendor.id, PricingQuote.freshness_state == "current")
        .order_by(PricingQuote.recorded_at.desc())
        .limit(20)
        .all()
    )
    recent_prices = [_safe_float(q.unit_price, 0.0) for q in quotes if _safe_float(q.unit_price, 0.0) > 0]
    avg_price = sum(recent_prices) / len(recent_prices) if recent_prices else 0.0

    if baseline_unit_price <= 0:
        score = 0.65 + (_safe_float(vendor.reliability_score, 0.7) * 0.1)
    elif avg_price <= 0:
        score = 0.68
    else:
        diff = abs(avg_price - baseline_unit_price) / baseline_unit_price
        score = max(0.0, 1.0 - min(diff, 1.0))

    return score, {
        "average_unit_price": round(avg_price, 4),
        "baseline_unit_price": round(baseline_unit_price, 4),
        "recent_quotes": len(recent_prices),
    }


def _score_quality(db: Session, vendor: Vendor, memory: Dict[str, Any]) -> float:
    reliability = _safe_float(vendor.reliability_score, 0.7)
    performance = _safe_float(memory.get("performance_score"), 0.5)
    delivery = _safe_float(memory.get("delivery_accuracy_score"), 0.5)
    return round((reliability * 0.45) + (performance * 0.35) + (delivery * 0.20), 6)


def _score_response_history(memory: Dict[str, Any]) -> float:
    orders = _safe_float(memory.get("total_orders"), 0.0)
    perf = _safe_float(memory.get("performance_score"), 0.5)
    order_signal = min(1.0, log1p(max(orders, 0.0)) / log1p(25.0))
    return round((order_signal * 0.45) + (perf * 0.55), 6)


def _score_logistics(db: Session, vendor: Vendor, delivery_region: str) -> Tuple[float, Dict[str, Any]]:
    if not delivery_region:
        return 0.7, {"delivery_region": "", "distance_km": None}

    region_profile = db.query(RegionProfile).filter(RegionProfile.region_name == vendor.region).first()
    target_profile = db.query(RegionProfile).filter(RegionProfile.region_name == delivery_region).first()

    distance_km = None
    if region_profile and region_profile.distance_km:
        distance_km = region_profile.distance_km.get(delivery_region)
    elif vendor.region and _normalize_text(vendor.region) == _normalize_text(delivery_region):
        distance_km = 0

    if distance_km is None and target_profile and target_profile.distance_km:
        distance_km = target_profile.distance_km.get(vendor.region)

    if distance_km is None:
        if _normalize_text(vendor.region) == _normalize_text(delivery_region):
            score = 0.98
        elif _normalize_text(vendor.country) == _normalize_text(delivery_region):
            score = 0.9
        else:
            score = 0.68
        return score, {"delivery_region": delivery_region, "distance_km": None}

    score = max(0.0, 1.0 - min(_safe_float(distance_km, 0.0) / 12000.0, 1.0) * 0.45)
    return score, {"delivery_region": delivery_region, "distance_km": _safe_float(distance_km, 0.0)}


def _score_tariff(db: Session, vendor: Vendor, delivery_region: str, category: str) -> Tuple[float, Dict[str, Any]]:
    rule = (
        db.query(TariffRule)
        .filter(TariffRule.origin_region == vendor.region)
        .filter(TariffRule.destination_region == delivery_region)
        .first()
    )
    tariff_pct = _safe_float(rule.tariff_pct, 0.0) if rule else 0.0

    if tariff_pct <= 0:
        score = 0.92
    else:
        score = max(0.0, 1.0 - min(tariff_pct * 4.0, 1.0))

    return score, {
        "origin_region": vendor.region,
        "destination_region": delivery_region,
        "product_category": category,
        "tariff_pct": round(tariff_pct, 4),
        "rule_found": bool(rule),
    }


def _score_currency(vendor: Vendor, target_currency: str) -> Tuple[float, Dict[str, Any]]:
    vendor_currency = (vendor.metadata_ or {}).get("currency", "USD")
    if not target_currency:
        target_currency = "USD"
    if _normalize_text(vendor_currency) == _normalize_text(target_currency):
        return 1.0, {"vendor_currency": vendor_currency, "target_currency": target_currency}
    if _normalize_text(target_currency) == "usd":
        return 0.88, {"vendor_currency": vendor_currency, "target_currency": target_currency}
    return 0.8, {"vendor_currency": vendor_currency, "target_currency": target_currency}


def _score_geography(vendor: Vendor, filters: Dict[str, Any], delivery_region: str) -> Tuple[float, Dict[str, Any]]:
    allowed_regions = _split_csv(filters.get("regions"))
    if allowed_regions:
        if vendor.region in allowed_regions or vendor.country in allowed_regions:
            return 1.0, {"allowed_regions": allowed_regions, "matched": True}
        return 0.0, {"allowed_regions": allowed_regions, "matched": False}

    if delivery_region and (_normalize_text(vendor.region) == _normalize_text(delivery_region) or _normalize_text(vendor.country) == _normalize_text(delivery_region)):
        return 1.0, {"delivery_region": delivery_region, "matched": True}

    return 0.72, {"delivery_region": delivery_region, "matched": False}


def _require_certifications(vendor: Vendor, required: List[str]) -> bool:
    if not required:
        return True
    certs = {c.lower() for c in _vendor_certifications(vendor)}
    return all(r.lower() in certs for r in required)


def _vendor_avg_moq(vendor: Vendor) -> float:
    vals = []
    for cap in vendor.capability_entries or []:
        if cap.min_quantity is not None:
            vals.append(_safe_float(cap.min_quantity, 0.0))
    return min(vals) if vals else 0.0


def _vendor_avg_lead_time(vendor: Vendor) -> float:
    vals = []
    for cap in vendor.capability_entries or []:
        if cap.typical_lead_days is not None:
            vals.append(_safe_float(cap.typical_lead_days, 0.0))
    if vals:
        return sum(vals) / len(vals)
    return _safe_float(vendor.avg_lead_time_days, 14.0)


def score_vendor_for_project(db: Session, project, vendor: Vendor, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    filters = filters or {}
    parts = _extract_project_parts(project)
    if not parts:
        parts = [{
            "part_name": "Project",
            "category": "unknown",
            "process": "",
            "material": "",
            "quantity": 1.0,
            "best_region": project.recommended_location or "",
            "best_cost": _safe_float(project.average_cost, 0.0),
            "lead_days": _safe_float(project.lead_time, 14.0),
        }]

    delivery_region = (
        filters.get("delivery_region")
        or (project.strategy or {}).get("bom_summary", {}).get("delivery_location")
        or project.recommended_location
        or ""
    )
    target_currency = filters.get("currency") or (project.project_metadata or {}).get("currency") or "USD"
    memory = _vendor_memory_snapshot(db, vendor)

    required_certs = _split_csv(filters.get("certifications"))
    if not _require_certifications(vendor, required_certs):
        return {"eligible": False, "reason": "certification_filter", "vendor_id": vendor.id}

    max_lead = _safe_float(filters.get("max_lead_time"), 0.0)
    vendor_lead = _vendor_avg_lead_time(vendor)
    if max_lead > 0 and vendor_lead > max_lead:
        return {"eligible": False, "reason": "lead_time_filter", "vendor_id": vendor.id}

    max_moq = _safe_float(filters.get("max_moq"), 0.0)
    vendor_moq = _vendor_avg_moq(vendor)
    if max_moq > 0 and vendor_moq > max_moq:
        return {"eligible": False, "reason": "moq_filter", "vendor_id": vendor.id}

    search = _normalize_text(filters.get("search"))
    if search:
        searchable = " ".join([
            vendor.name or "",
            vendor.legal_name or "",
            vendor.region or "",
            vendor.country or "",
            " ".join(_vendor_capability_tokens(vendor)),
        ]).lower()
        if search not in searchable:
            return {"eligible": False, "reason": "search_filter", "vendor_id": vendor.id}

    baseline_unit_price = 0.0
    if project.average_cost and project.total_parts:
        baseline_unit_price = _safe_float(project.average_cost, 0.0) / max(int(project.total_parts or 1), 1)

    part_rationales: List[Dict[str, Any]] = []
    score_bucket = defaultdict(float)
    weight_bucket = defaultdict(float)
    total_weight = 0.0

    for part in parts:
        qty = max(_safe_float(part.get("quantity"), 1.0), 1.0)
        total_weight += qty
        cap = _best_capability_for_part(part, vendor)

        process_fit = _score_overlap(part.get("process"), cap.get("process"))
        material_fit = _score_overlap(part.get("material"), cap.get("material_family"))
        capacity_fit = _score_capacity(qty, cap)
        lead_time_fit = _score_lead_time(_safe_float(part.get("lead_days"), _safe_float(project.lead_time, 14.0)), cap.get("typical_lead_days"))
        price_fit, price_details = _score_price(db, vendor, baseline_unit_price)
        quality_fit = _score_quality(db, vendor, memory)
        response_fit = _score_response_history(memory)
        logistics_fit, logistics_details = _score_logistics(db, vendor, delivery_region)
        tariff_fit, tariff_details = _score_tariff(db, vendor, delivery_region, part.get("category") or "unknown")
        currency_fit, currency_details = _score_currency(vendor, target_currency)

        weights = {
            "process_fit": 0.17,
            "material_fit": 0.08,
            "capacity_fit": 0.10,
            "price_fit": 0.15,
            "lead_time_fit": 0.12,
            "quality_fit": 0.14,
            "response_history": 0.08,
            "logistics_fit": 0.08,
            "tariff_fit": 0.05,
            "currency_fit": 0.03,
        }

        part_score = (
            process_fit * weights["process_fit"] +
            material_fit * weights["material_fit"] +
            capacity_fit * weights["capacity_fit"] +
            price_fit * weights["price_fit"] +
            lead_time_fit * weights["lead_time_fit"] +
            quality_fit * weights["quality_fit"] +
            response_fit * weights["response_history"] +
            logistics_fit * weights["logistics_fit"] +
            tariff_fit * weights["tariff_fit"] +
            currency_fit * weights["currency_fit"]
        )

        score_bucket["process_fit"] += process_fit * qty
        score_bucket["material_fit"] += material_fit * qty
        score_bucket["capacity_fit"] += capacity_fit * qty
        score_bucket["price_fit"] += price_fit * qty
        score_bucket["lead_time_fit"] += lead_time_fit * qty
        score_bucket["quality_fit"] += quality_fit * qty
        score_bucket["response_history"] += response_fit * qty
        score_bucket["logistics_fit"] += logistics_fit * qty
        score_bucket["tariff_fit"] += tariff_fit * qty
        score_bucket["currency_fit"] += currency_fit * qty

        weight_bucket["process_fit"] += qty
        weight_bucket["material_fit"] += qty
        weight_bucket["capacity_fit"] += qty
        weight_bucket["price_fit"] += qty
        weight_bucket["lead_time_fit"] += qty
        weight_bucket["quality_fit"] += qty
        weight_bucket["response_history"] += qty
        weight_bucket["logistics_fit"] += qty
        weight_bucket["tariff_fit"] += qty
        weight_bucket["currency_fit"] += qty

        reason_codes = []
        if process_fit >= 0.8:
            reason_codes.append("PROCESS_MATCH")
        if material_fit >= 0.7:
            reason_codes.append("MATERIAL_MATCH")
        if capacity_fit >= 0.8:
            reason_codes.append("CAPACITY_OK")
        if price_fit >= 0.7:
            reason_codes.append("PRICE_COMPETITIVE")
        if lead_time_fit >= 0.8:
            reason_codes.append("LEAD_TIME_OK")
        if quality_fit >= 0.75:
            reason_codes.append("QUALITY_STRONG")
        if response_fit >= 0.7:
            reason_codes.append("RESPONSIVE_SUPPLIER")
        if logistics_fit >= 0.75:
            reason_codes.append("LOGISTICS_ALIGNED")
        if tariff_fit >= 0.75:
            reason_codes.append("LOW_TARIFF_EXPOSURE")
        if currency_fit >= 0.75:
            reason_codes.append("CURRENCY_ALIGNED")

        part_rationales.append({
            "part_name": part.get("part_name"),
            "category": part.get("category"),
            "quantity": qty,
            "selected_capability": cap,
            "process_fit": round(process_fit, 4),
            "material_fit": round(material_fit, 4),
            "capacity_fit": round(capacity_fit, 4),
            "price_fit": round(price_fit, 4),
            "lead_time_fit": round(lead_time_fit, 4),
            "quality_fit": round(quality_fit, 4),
            "response_history": round(response_fit, 4),
            "logistics_fit": round(logistics_fit, 4),
            "tariff_fit": round(tariff_fit, 4),
            "currency_fit": round(currency_fit, 4),
            "price_details": price_details,
            "logistics_details": logistics_details,
            "tariff_details": tariff_details,
            "currency_details": currency_details,
            "overall_part_score": round(part_score, 6),
            "reason_codes": list(dict.fromkeys(reason_codes)),
        })

    weight_divisor = max(total_weight, 1.0)
    overall = 0.0
    for metric, subtotal in score_bucket.items():
        overall += (subtotal / weight_divisor) * {
            "process_fit": 0.17,
            "material_fit": 0.08,
            "capacity_fit": 0.10,
            "price_fit": 0.15,
            "lead_time_fit": 0.12,
            "quality_fit": 0.14,
            "response_history": 0.08,
            "logistics_fit": 0.08,
            "tariff_fit": 0.05,
            "currency_fit": 0.03,
        }[metric]

    geo_score, geo_details = _score_geography(vendor, filters, delivery_region)
    overall = min(1.0, max(0.0, overall * (0.88 + geo_score * 0.12)))

    top_strengths = sorted(
        [
            ("process_fit", _safe_float(score_bucket["process_fit"] / weight_divisor, 0.0)),
            ("quality_fit", _safe_float(score_bucket["quality_fit"] / weight_divisor, 0.0)),
            ("price_fit", _safe_float(score_bucket["price_fit"] / weight_divisor, 0.0)),
            ("lead_time_fit", _safe_float(score_bucket["lead_time_fit"] / weight_divisor, 0.0)),
        ],
        key=lambda x: x[1],
        reverse=True,
    )[:3]

    reason_codes = [code for pr in part_rationales for code in pr["reason_codes"]]
    if geo_score >= 0.9:
        reason_codes.append("GEOGRAPHY_FIT")
    elif geo_score == 0:
        reason_codes.append("GEOGRAPHY_FILTER_FAIL")

    explanation = {
        "summary": f"{vendor.name} scores {overall:.4f} on project match",
        "top_strengths": [k for k, _ in top_strengths],
        "geo_details": geo_details,
        "part_rationale_count": len(part_rationales),
        "filter_constraints": filters,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "risks": [
            code for code, value in [
                ("PRICE_WEAK", _safe_float(score_bucket["price_fit"] / weight_divisor, 0.0)),
                ("LEAD_TIME_WEAK", _safe_float(score_bucket["lead_time_fit"] / weight_divisor, 0.0)),
                ("QUALITY_WEAK", _safe_float(score_bucket["quality_fit"] / weight_divisor, 0.0)),
            ]
            if value < 0.55
        ],
    }

    scorecard = {
        "overall_score": round(overall, 6),
        "subscores": {
            "process_fit": round(score_bucket["process_fit"] / weight_divisor, 6),
            "material_fit": round(score_bucket["material_fit"] / weight_divisor, 6),
            "capacity_fit": round(score_bucket["capacity_fit"] / weight_divisor, 6),
            "price_fit": round(score_bucket["price_fit"] / weight_divisor, 6),
            "lead_time_fit": round(score_bucket["lead_time_fit"] / weight_divisor, 6),
            "quality_fit": round(score_bucket["quality_fit"] / weight_divisor, 6),
            "response_history": round(score_bucket["response_history"] / weight_divisor, 6),
            "logistics_fit": round(score_bucket["logistics_fit"] / weight_divisor, 6),
            "tariff_fit": round(score_bucket["tariff_fit"] / weight_divisor, 6),
            "currency_fit": round(score_bucket["currency_fit"] / weight_divisor, 6),
            "geography_fit": round(geo_score, 6),
        },
        "memory": memory,
        "price_signal": {"baseline_unit_price": round(baseline_unit_price, 4)},
        "part_rationales": part_rationales,
        "constraints": filters,
        "delivery_region": delivery_region,
        "target_currency": target_currency,
    }

    return {
        "eligible": True,
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
        "region": vendor.region,
        "country": vendor.country,
        "reliability_score": _safe_float(vendor.reliability_score, 0.7),
        "avg_lead_time_days": _safe_float(vendor.avg_lead_time_days, vendor_lead),
        "certifications": _vendor_certifications(vendor),
        "capabilities": _vendor_capability_tokens(vendor),
        "memory": memory,
        "part_rationales": part_rationales,
        "score_breakdown": scorecard["subscores"],
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "explanation_json": explanation,
        "scorecard_json": scorecard,
        "overall_score": round(overall, 6),
        "price_summary": {
            "baseline_unit_price": round(baseline_unit_price, 4),
            "vendor_avg_moq": round(vendor_moq, 4),
            "vendor_avg_lead_time": round(vendor_lead, 4),
        },
        "match_constraints": filters,
    }


def get_vendor_profile(db: Session, vendor_id: str) -> Optional[Dict[str, Any]]:
    vendor = (
        db.query(Vendor)
        .options(joinedload(Vendor.capability_entries), joinedload(Vendor.memory))
        .filter(Vendor.id == vendor_id)
        .first()
    )
    if not vendor:
        return None

    memory = _vendor_memory_snapshot(db, vendor)
    return {
        "id": vendor.id,
        "name": vendor.name,
        "legal_name": vendor.legal_name,
        "country": vendor.country,
        "region": vendor.region,
        "website": vendor.website,
        "contact_email": vendor.contact_email,
        "contact_phone": vendor.contact_phone,
        "reliability_score": _safe_float(vendor.reliability_score, 0.7),
        "avg_lead_time_days": _safe_float(vendor.avg_lead_time_days, 14.0),
        "certifications": _vendor_certifications(vendor),
        "capabilities": _vendor_capability_tokens(vendor),
        "memory": memory,
        "capability_entries": [
            {
                "id": cap.id,
                "process": cap.process,
                "material_family": cap.material_family,
                "proficiency": _safe_float(cap.proficiency, 0.0),
                "min_quantity": _safe_float(cap.min_quantity, None),
                "max_quantity": _safe_float(cap.max_quantity, None),
                "typical_lead_days": _safe_float(cap.typical_lead_days, None),
                "certifications": list(cap.certifications or []),
                "notes": cap.notes,
                "is_active": bool(cap.is_active),
            }
            for cap in (vendor.capability_entries or [])
        ],
    }


def _project_context(db: Session, project_id: str):
    from app.services.project_service import get_project_by_id, get_project_by_bom_id

    project = get_project_by_id(db, project_id)
    if not project:
        project = get_project_by_bom_id(db, project_id)
    return project


def build_vendor_scorecard(db: Session, vendor_id: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    vendor = (
        db.query(Vendor)
        .options(joinedload(Vendor.capability_entries), joinedload(Vendor.memory))
        .filter(Vendor.id == vendor_id)
        .first()
    )
    if not vendor:
        return None

    profile = get_vendor_profile(db, vendor_id)
    memory = _vendor_memory_snapshot(db, vendor)

    pricing_quotes = (
        db.query(PricingQuote)
        .filter(PricingQuote.vendor_id == vendor.id)
        .order_by(PricingQuote.recorded_at.desc())
        .limit(10)
        .all()
    )
    pricing_summary = {
        "recent_quotes": len(pricing_quotes),
        "avg_unit_price": round(sum(_safe_float(q.unit_price, 0.0) for q in pricing_quotes) / max(len(pricing_quotes), 1), 4),
        "avg_lead_time_days": round(sum(_safe_float(q.lead_time_days, 0.0) for q in pricing_quotes) / max(len(pricing_quotes), 1), 4),
        "currencies": list(dict.fromkeys([q.display_currency for q in pricing_quotes if q.display_currency])),
    }

    recent_matches = (
        db.query(VendorMatch)
        .filter(VendorMatch.vendor_id == vendor.id)
        .order_by(VendorMatch.created_at.desc())
        .limit(10)
        .all()
    )

    project_match = None
    if project_id:
        project = _project_context(db, project_id)
        if project:
            score = score_vendor_for_project(db, project, vendor, filters={"delivery_region": project.recommended_location or ""})
            project_match = {
                "project_id": project.id,
                "project_name": project.name,
                "match": score,
            }

    latest_match = None
    if recent_matches:
        m = recent_matches[0]
        latest_match = {
            "match_id": m.id,
            "project_id": m.project_id,
            "match_run_id": m.match_run_id,
            "rank": m.rank,
            "score": _safe_float(m.score, 0.0),
            "reason_codes": m.reason_codes or [],
            "score_breakdown": m.score_breakdown or {},
            "explanation_json": m.explanation_json or {},
            "shortlist_status": m.shortlist_status,
            "response_status": m.response_status,
            "feedback_rating": _safe_float(m.feedback_rating, None),
            "feedback_notes": m.feedback_notes,
            "created_at": m.created_at,
        }

    return {
        "vendor": profile,
        "project_id": project_id,
        "project_match": project_match,
        "current_memory": memory,
        "pricing_summary": pricing_summary,
        "capability_summary": {
            "count": len(profile["capability_entries"]),
            "processes": profile["capabilities"],
        },
        "recent_matches": [
            {
                "match_id": m.id,
                "project_id": m.project_id,
                "match_run_id": m.match_run_id,
                "rank": m.rank,
                "score": _safe_float(m.score, 0.0),
                "reason_codes": m.reason_codes or [],
                "shortlist_status": m.shortlist_status,
                "response_status": m.response_status,
                "created_at": m.created_at,
            }
            for m in recent_matches
        ],
        "scorecard": {
            "vendor_id": vendor.id,
            "vendor_name": vendor.name,
            "memory": memory,
            "pricing_summary": pricing_summary,
            "latest_match": latest_match,
        },
    }


def match_vendors_for_project(
    db: Session,
    project_id: str,
    user_id: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    from app.services.project_service import normalize_project_stage, project_stage_action, record_project_event

    filters = filters or {}
    project = _project_context(db, project_id)
    if not project:
        raise ValueError("Project not found")

    vendors = (
        db.query(Vendor)
        .options(joinedload(Vendor.capability_entries), joinedload(Vendor.memory))
        .filter(Vendor.is_active == True)
        .all()
    )

    scored = []
    for vendor in vendors:
        result = score_vendor_for_project(db, project, vendor, filters=filters)
        if result.get("eligible"):
            scored.append(result)

    scored.sort(key=lambda r: r.get("overall_score", 0), reverse=True)
    shortlist = scored[: max(1, min(int(limit or 20), 50))]

    run = VendorMatchRun(
        project_id=project.id,
        user_id=user_id or project.user_id,
        filters_json=filters,
        constraints_json={
            "delivery_region": filters.get("delivery_region") or project.recommended_location or "",
            "currency": filters.get("currency") or (project.project_metadata or {}).get("currency") or "USD",
            "search": filters.get("search"),
            "regions": _split_csv(filters.get("regions")),
            "certifications": _split_csv(filters.get("certifications")),
            "max_moq": filters.get("max_moq"),
            "max_lead_time": filters.get("max_lead_time"),
            "max_price": filters.get("max_price"),
        },
        strategy_snapshot=project.strategy or {},
        analysis_snapshot=project.analyzer_report or {},
        weights_json={
            "process_fit": 0.17,
            "material_fit": 0.08,
            "capacity_fit": 0.10,
            "price_fit": 0.15,
            "lead_time_fit": 0.12,
            "quality_fit": 0.14,
            "response_history": 0.08,
            "logistics_fit": 0.08,
            "tariff_fit": 0.05,
            "currency_fit": 0.03,
        },
        summary_json={
            "top_vendor": shortlist[0]["vendor_name"] if shortlist else None,
            "top_score": shortlist[0]["overall_score"] if shortlist else 0,
            "top_reason_codes": shortlist[0]["reason_codes"] if shortlist else [],
            "project_stage": project.workflow_stage or project.status,
        },
        total_vendors_considered=len(vendors),
        total_matches=len(scored),
        shortlist_size=len(shortlist),
    )
    db.add(run)
    db.flush()

    items = []
    for rank, result in enumerate(shortlist, start=1):
        vendor = db.query(Vendor).filter(Vendor.id == result["vendor_id"]).first()
        match_row = VendorMatch(
            match_run_id=run.id,
            project_id=project.id,
            vendor_id=vendor.id,
            rank=rank,
            score=result["overall_score"],
            score_breakdown=result["score_breakdown"],
            reason_codes=result["reason_codes"],
            explanation_json=result["explanation_json"],
            constraint_inputs=result["match_constraints"],
            scorecard_json=result["scorecard_json"],
            part_rationales=result["part_rationales"],
            shortlist_status="shortlisted",
            response_status="uncontacted",
            is_primary=(rank == 1),
        )
        db.add(match_row)
        db.flush()

        items.append({
            "match_id": match_row.id,
            "project_id": project.id,
            "match_run_id": run.id,
            "vendor_id": vendor.id,
            "vendor_name": vendor.name,
            "region": vendor.region,
            "country": vendor.country,
            "rank": rank,
            "score": _safe_float(result["overall_score"], 0.0),
            "reason_codes": result["reason_codes"],
            "explanation_json": result["explanation_json"],
            "score_breakdown": result["score_breakdown"],
            "constraint_inputs": result["match_constraints"],
            "part_rationales": result["part_rationales"],
            "shortlist_status": "shortlisted",
            "response_status": "uncontacted",
            "feedback_rating": None,
            "feedback_notes": None,
            "certifications": result["certifications"],
            "capabilities": result["capabilities"],
            "avg_lead_time_days": result["avg_lead_time_days"],
            "reliability_score": result["reliability_score"],
            "memory": result["memory"],
            "pricing_summary": result["price_summary"],
            "scorecard_json": result["scorecard_json"],
        })

    # Normalize project state into vendor matching
    old_stage = project.workflow_stage or project.status
    if normalize_project_stage(old_stage, "project_hydrated") in {"project_hydrated", "strategy", "vendor_match"}:
        project.workflow_stage = "vendor_match"
        project.status = "vendor_match"
        project.current_vendor_match_id = run.id
        project.project_metadata = project.project_metadata or {}
        project.project_metadata["workflow_stage"] = "vendor_match"
        project.project_metadata["next_action"] = project_stage_action("vendor_match")
        project.project_metadata["vendor_match_filters"] = filters

        record_project_event(
            db,
            project,
            "vendor_match_run_created",
            old_stage,
            "vendor_match",
            {"vendor_match_run_id": run.id, "shortlist_size": len(shortlist), "filters": filters},
            actor_user_id=user_id,
        )

    db.flush()

    return {
        "run_id": run.id,
        "project_id": project.id,
        "user_id": run.user_id,
        "filters_json": run.filters_json,
        "constraints_json": run.constraints_json,
        "strategy_snapshot": run.strategy_snapshot,
        "analysis_snapshot": run.analysis_snapshot,
        "weights_json": run.weights_json,
        "summary_json": run.summary_json,
        "total_vendors_considered": run.total_vendors_considered,
        "total_matches": run.total_matches,
        "shortlist_size": run.shortlist_size,
        "items": items,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def record_vendor_feedback(db: Session, vendor_id: str, payload: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    from app.services.memory_service import update_supplier_scores

    rating = _safe_float(payload.get("rating"), None)
    quality_ok = payload.get("quality_ok")
    if quality_ok is None and rating is not None:
        quality_ok = rating >= 4.0

    result = update_supplier_scores(
        db,
        vendor_id=vendor_id,
        actual_cost=payload.get("actual_cost"),
        predicted_cost=payload.get("predicted_cost"),
        actual_lead=payload.get("actual_lead_days"),
        predicted_lead=payload.get("predicted_lead_days"),
        quality_ok=bool(quality_ok) if quality_ok is not None else True,
    )

    match_row = None
    match_run_id = payload.get("match_run_id")
    project_id = payload.get("project_id")
    if match_run_id and project_id:
        match_row = (
            db.query(VendorMatch)
            .filter(VendorMatch.match_run_id == match_run_id)
            .filter(VendorMatch.project_id == project_id)
            .filter(VendorMatch.vendor_id == vendor_id)
            .first()
        )
    elif project_id:
        match_row = (
            db.query(VendorMatch)
            .filter(VendorMatch.project_id == project_id)
            .filter(VendorMatch.vendor_id == vendor_id)
            .order_by(VendorMatch.created_at.desc())
            .first()
        )

    if match_row:
        match_row.feedback_rating = rating
        match_row.feedback_notes = payload.get("notes")
        if payload.get("response_status"):
            match_row.response_status = payload.get("response_status")
        match_row.shortlist_status = "feedback_received"
        db.flush()

    return {
        "status": "updated",
        "memory_result": result,
        "vendor_id": vendor_id,
        "project_id": project_id,
        "match_run_id": match_run_id,
        "rating": rating,
        "notes": payload.get("notes"),
    }
