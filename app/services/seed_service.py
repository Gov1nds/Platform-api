"""
Seed Service — populate canonical part master with industry-specific examples.

Seeds mechanical, electrical, electronics, fasteners, raw material, and
sheet metal canonical entries with aliases. Run once on startup if DB is empty.
These are the "gold set" that bootstraps matching accuracy.
"""
import logging
from typing import List, Dict
from sqlalchemy.orm import Session

from app.models.catalog import PartMaster, PartAlias, PartAttribute

logger = logging.getLogger("seed_service")
_seeded = False

# ═══════════════════════════════════════════════════════════
# SEED DATA — canonical parts with aliases and key attributes
# ═══════════════════════════════════════════════════════════

SEED_PARTS: List[Dict] = [
    # ── Electrical ──
    {"key": "electrical:resistor:0805:10k", "domain": "electrical", "cat": "electrical", "desc": "10kΩ Resistor 0805 1%",
     "mpn": "RC0805FR-0710KL", "mfr": "yageo", "aliases": [("mpn", "RC0805FR0710KL"), ("description", "10k resistor 0805")],
     "attrs": [("resistance_ohm", "10000", "ohm"), ("package", "0805", None), ("tolerance_pct", "1", "%")]},
    {"key": "electrical:capacitor:0805:100nf", "domain": "electrical", "cat": "electrical", "desc": "100nF Capacitor 0805 X7R 50V",
     "mpn": "GRM21BR71H104KA01", "mfr": "murata", "aliases": [("mpn", "GRM21BR71H104KA01L"), ("description", "100nf capacitor 0805")],
     "attrs": [("capacitance_f", "0.0000001", "F"), ("package", "0805", None), ("voltage_v", "50", "V")]},
    {"key": "electrical:inductor:1210:10uh", "domain": "electrical", "cat": "electrical", "desc": "10µH Inductor 1210",
     "mpn": "MLZ3215N100LT000", "mfr": "tdk", "aliases": [("description", "10uh inductor 1210")],
     "attrs": [("inductance_h", "0.00001", "H"), ("package", "1210", None)]},

    # ── Electronics ──
    {"key": "electronics:mpn:texasinstruments:LM7805", "domain": "electronics", "cat": "electronics", "desc": "5V Linear Voltage Regulator TO-220",
     "mpn": "LM7805CT", "mfr": "texas instruments", "aliases": [("mpn", "LM7805"), ("mpn", "UA7805"), ("description", "5v voltage regulator")],
     "attrs": [("voltage_v", "5", "V"), ("package", "TO-220", None)]},
    {"key": "electronics:mpn:stmicroelectronics:STM32F103", "domain": "electronics", "cat": "electronics", "desc": "STM32F103 ARM Cortex-M3 MCU LQFP-64",
     "mpn": "STM32F103RBT6", "mfr": "stmicroelectronics", "aliases": [("mpn", "STM32F103"), ("description", "stm32 microcontroller")],
     "attrs": [("package", "LQFP-64", None), ("component_type", "microcontroller", None)]},
    {"key": "electronics:mpn:espressif:ESP32", "domain": "electronics", "cat": "electronics", "desc": "ESP32-WROOM-32 WiFi+BT Module",
     "mpn": "ESP32-WROOM-32E", "mfr": "espressif", "aliases": [("mpn", "ESP32WROOM32"), ("description", "esp32 wifi module")],
     "attrs": [("component_type", "module", None)]},

    # ── Fasteners ──
    {"key": "fastener:hex_bolt:m8x25:ss304", "domain": "fastener", "cat": "fastener", "desc": "Hex Bolt M8x25 SS304 DIN933",
     "mpn": "", "mfr": "", "aliases": [("description", "hex bolt m8x25 stainless"), ("description", "m8 x 25 hex bolt ss304")],
     "attrs": [("thread_size", "M8", "mm"), ("length_mm", "25", "mm"), ("material_grade", "304", None), ("standard", "DIN933", None)]},
    {"key": "fastener:screw:m4x12:ss304", "domain": "fastener", "cat": "fastener", "desc": "Socket Head Cap Screw M4x12 SS304",
     "mpn": "", "mfr": "", "aliases": [("description", "shcs m4x12 stainless"), ("description", "socket head m4 x 12")],
     "attrs": [("thread_size", "M4", "mm"), ("length_mm", "12", "mm"), ("head_type", "socket_head", None)]},
    {"key": "fastener:nut:m8:ss304", "domain": "fastener", "cat": "fastener", "desc": "Hex Nut M8 SS304 DIN934",
     "mpn": "", "mfr": "", "aliases": [("description", "hex nut m8 stainless"), ("description", "m8 nut ss304")],
     "attrs": [("thread_size", "M8", "mm"), ("standard", "DIN934", None)]},

    # ── Raw Materials ──
    {"key": "raw_material:sheet:stainless_steel_304", "domain": "raw_material", "cat": "raw_material", "desc": "SS304 Sheet 2mm",
     "mpn": "", "mfr": "", "aliases": [("description", "ss304 sheet"), ("description", "stainless steel 304 sheet 2mm")],
     "attrs": [("material_grade", "304", None), ("form", "sheet", None), ("thickness_mm", "2", "mm")]},
    {"key": "raw_material:bar:aluminum_6061", "domain": "raw_material", "cat": "raw_material", "desc": "Aluminum 6061-T6 Round Bar",
     "mpn": "", "mfr": "", "aliases": [("description", "al6061 round bar"), ("description", "aluminum 6061 bar stock")],
     "attrs": [("material_grade", "6061", None), ("temper", "T6", None), ("form", "bar", None)]},
    {"key": "raw_material:bar:mild_steel", "domain": "raw_material", "cat": "raw_material", "desc": "Mild Steel Round Bar",
     "mpn": "", "mfr": "", "aliases": [("description", "ms round bar"), ("description", "mild steel bar stock")],
     "attrs": [("material_family", "carbon_steel", None), ("form", "bar", None)]},

    # ── Machined ──
    {"key": "machined:shaft:ss304:custom", "domain": "machined", "cat": "machined", "desc": "CNC Machined Shaft SS304 Ø25x150mm",
     "mpn": "", "mfr": "", "aliases": [("description", "machined shaft stainless"), ("description", "cnc shaft ss304")],
     "attrs": [("material_grade", "304", None), ("diameter_mm", "25", "mm"), ("length_mm", "150", "mm")]},
    {"key": "machined:bushing:bronze:custom", "domain": "machined", "cat": "machined", "desc": "CNC Turned Bronze Bushing",
     "mpn": "", "mfr": "", "aliases": [("description", "bronze bushing machined"), ("description", "cnc turned bushing")],
     "attrs": [("material_family", "bronze", None), ("geometry_class", "shaft", None)]},

    # ── Sheet Metal ──
    {"key": "sheet_metal:bracket:ms:custom", "domain": "sheet_metal", "cat": "sheet_metal", "desc": "Laser Cut MS Bracket 3mm",
     "mpn": "", "mfr": "", "aliases": [("description", "sheet metal bracket mild steel"), ("description", "laser cut bracket ms")],
     "attrs": [("material_family", "carbon_steel", None), ("thickness_mm", "3", "mm"), ("form", "sheet", None)]},
    {"key": "sheet_metal:panel:al:custom", "domain": "sheet_metal", "cat": "sheet_metal", "desc": "Aluminum Panel 2mm Powder Coated",
     "mpn": "", "mfr": "", "aliases": [("description", "aluminum panel powder coated"), ("description", "al panel laser cut")],
     "attrs": [("material_family", "aluminum", None), ("coating", "powder_coated", None)]},

    # ── Custom Mechanical ──
    {"key": "custom_mechanical:housing:al6061:custom", "domain": "custom_mechanical", "cat": "custom_mechanical", "desc": "CNC Machined Aluminum Housing 6061-T6",
     "mpn": "", "mfr": "", "aliases": [("description", "aluminum housing cnc"), ("description", "machined enclosure al6061")],
     "attrs": [("material_grade", "6061", None), ("geometry_class", "bracket", None)]},

    # ── Standard ──
    {"key": "standard:bearing:6205:skf", "domain": "standard", "cat": "standard", "desc": "Deep Groove Ball Bearing 6205-2RS SKF",
     "mpn": "6205-2RS1", "mfr": "skf", "aliases": [("mpn", "62052RS"), ("description", "bearing 6205 skf")],
     "attrs": [("bearing_type", "deep_groove", None)]},
]


def seed_canonical_parts(db: Session):
    """Seed canonical parts if the part_master table is empty."""
    global _seeded
    if _seeded:
        return

    try:
        count = db.query(PartMaster).count()
        if count >= len(SEED_PARTS):
            _seeded = True
            return

        created = 0
        for sp in SEED_PARTS:
            existing = db.query(PartMaster).filter(PartMaster.canonical_part_key == sp["key"]).first()
            if existing:
                continue

            pm = PartMaster(
                canonical_part_key=sp["key"],
                domain=sp["domain"],
                category=sp["cat"],
                procurement_class="catalog_purchase" if sp["cat"] in ("standard", "electrical", "electronics", "fastener") else (
                    "machined_part" if sp["cat"] == "machined" else (
                        "raw_stock" if sp["cat"] == "raw_material" else "rfq_required"
                    )
                ),
                description=sp["desc"],
                mpn=sp.get("mpn", ""),
                manufacturer=sp.get("mfr", ""),
                review_status="seed",
                confidence=0.99,
                source="seed",
                observation_count=0,
            )
            db.add(pm)
            db.flush()

            # Aliases
            for alias_type, alias_val in sp.get("aliases", []):
                try:
                    db.add(PartAlias(
                        part_master_id=pm.id,
                        alias_type=alias_type,
                        alias_value=alias_val,
                        normalized_value=alias_val.strip().lower() if alias_type == "description" else alias_val.strip().upper(),
                    ))
                except Exception:
                    pass

            # Attributes
            for attr_key, attr_val, attr_unit in sp.get("attrs", []):
                try:
                    numeric = None
                    try:
                        numeric = float(attr_val)
                    except (ValueError, TypeError):
                        pass
                    db.add(PartAttribute(
                        part_master_id=pm.id,
                        attribute_key=attr_key,
                        attribute_value=attr_val,
                        attribute_unit=attr_unit,
                        numeric_value=numeric,
                        source="seed",
                        confidence=0.99,
                    ))
                except Exception:
                    pass

            created += 1

        if created:
            db.commit()
            logger.info(f"Seeded {created} canonical parts with aliases and attributes")
        _seeded = True

    except Exception as e:
        logger.warning(f"Canonical part seeding failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
