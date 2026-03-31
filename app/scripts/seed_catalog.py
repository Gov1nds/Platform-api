from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from app.core.database import SessionLocal
from app.models.catalog import PartMaster, PartAlias, PartAttribute


SEED_PARTS: List[Dict[str, Any]] = [
    {
        "canonical_part_key": "fastener_hex_bolt_m6_ss304",
        "domain": "mechanical",
        "category": "fastener",
        "procurement_class": "catalog_purchase",
        "description": "Hex bolt M6 x 20 SS304",
        "mpn": "M6X20-SS304-HEX-BOLT",
        "manufacturer": "Generic",
        "material": "stainless steel",
        "material_grade": "SS304",
        "material_form": "bolt",
        "specs": {
            "thread_size": "M6",
            "length_mm": 20,
            "diameter_mm": 6,
            "finish": "stainless",
            "standard": "DIN 933",
            "head_type": "hex",
        },
        "review_status": "seeded",
        "confidence": 0.95,
        "source": "seed",
        "aliases": [
            ("mpn", "M6X20-SS304-HEX-BOLT"),
            ("description", "M6 x 20 hex bolt SS304"),
            ("description", "SS304 hex bolt M6x20"),
            ("supplier_pn", "DIN933 M6x20 A2"),
        ],
    },
    {
        "canonical_part_key": "fastener_hex_nut_m6_ss304",
        "domain": "mechanical",
        "category": "fastener",
        "procurement_class": "catalog_purchase",
        "description": "Hex nut M6 SS304",
        "mpn": "M6-HEX-NUT-SS304",
        "manufacturer": "Generic",
        "material": "stainless steel",
        "material_grade": "SS304",
        "material_form": "nut",
        "specs": {
            "thread_size": "M6",
            "finish": "stainless",
            "standard": "DIN 934",
            "head_type": "hex",
        },
        "review_status": "seeded",
        "confidence": 0.95,
        "source": "seed",
        "aliases": [
            ("mpn", "M6-HEX-NUT-SS304"),
            ("description", "M6 nut stainless"),
            ("description", "SS304 hex nut M6"),
        ],
    },
    {
        "canonical_part_key": "electrical_resistor_10k_0805_1pct",
        "domain": "electrical",
        "category": "electrical",
        "procurement_class": "catalog_purchase",
        "description": "10k resistor 0805 1%",
        "mpn": "RES-10K-0805-1PCT",
        "manufacturer": "Generic",
        "material": "ceramic",
        "material_form": "smd",
        "specs": {
            "resistance_ohm": 10000,
            "package": "0805",
            "tolerance_pct": 1,
            "power_w": 0.125,
        },
        "review_status": "seeded",
        "confidence": 0.95,
        "source": "seed",
        "aliases": [
            ("mpn", "RES-10K-0805-1PCT"),
            ("description", "10k SMD resistor 0805"),
            ("description", "10000 ohm resistor 0805 1%"),
        ],
    },
    {
        "canonical_part_key": "electrical_capacitor_100nf_0603",
        "domain": "electrical",
        "category": "electrical",
        "procurement_class": "catalog_purchase",
        "description": "100nF capacitor 0603",
        "mpn": "CAP-100NF-0603",
        "manufacturer": "Generic",
        "material": "ceramic",
        "material_form": "smd",
        "specs": {
            "capacitance_f": 1e-7,
            "package": "0603",
            "tolerance_pct": 10,
            "voltage_v": 50,
        },
        "review_status": "seeded",
        "confidence": 0.95,
        "source": "seed",
        "aliases": [
            ("mpn", "CAP-100NF-0603"),
            ("description", "0.1uF capacitor 0603"),
            ("description", "100 nF MLCC 0603"),
        ],
    },
    {
        "canonical_part_key": "electronics_microswitch_spdt",
        "domain": "electronics",
        "category": "electronics",
        "procurement_class": "catalog_purchase",
        "description": "Microswitch SPDT",
        "mpn": "SW-MICRO-SPDT",
        "manufacturer": "Generic",
        "material": "plastic",
        "material_form": "switch",
        "specs": {
            "voltage_v": 250,
            "current_a": 5,
            "connector_type": "spdt",
        },
        "review_status": "seeded",
        "confidence": 0.9,
        "source": "seed",
        "aliases": [
            ("mpn", "SW-MICRO-SPDT"),
            ("description", "limit switch SPDT"),
            ("description", "micro switch SPDT"),
        ],
    },
    {
        "canonical_part_key": "electronics_relay_24v_spdt",
        "domain": "electronics",
        "category": "electronics",
        "procurement_class": "catalog_purchase",
        "description": "Relay 24V SPDT",
        "mpn": "RELAY-24V-SPDT",
        "manufacturer": "Generic",
        "material": "plastic",
        "material_form": "relay",
        "specs": {
            "voltage_v": 24,
            "current_a": 10,
            "connector_type": "spdt",
        },
        "review_status": "seeded",
        "confidence": 0.9,
        "source": "seed",
        "aliases": [
            ("mpn", "RELAY-24V-SPDT"),
            ("description", "24V relay SPDT"),
            ("description", "24 volt switching relay"),
        ],
    },
    {
        "canonical_part_key": "pneumatic_solenoid_valve_24v_1_4",
        "domain": "pneumatic",
        "category": "pneumatic",
        "procurement_class": "catalog_purchase",
        "description": "Pneumatic solenoid valve 24V 1/4",
        "mpn": "PNEU-SV-24V-14",
        "manufacturer": "Generic",
        "material": "aluminum",
        "material_form": "valve",
        "specs": {
            "voltage_v": 24,
            "pressure_bar": 10,
            "port_size": "1/4",
            "valve_type": "solenoid",
            "media": "air",
        },
        "review_status": "seeded",
        "confidence": 0.93,
        "source": "seed",
        "aliases": [
            ("mpn", "PNEU-SV-24V-14"),
            ("description", "air solenoid valve 24V 1/4"),
            ("description", "pneumatic valve 24V"),
        ],
    },
    {
        "canonical_part_key": "hydraulic_hose_3_8_high_pressure",
        "domain": "hydraulic",
        "category": "hydraulic",
        "procurement_class": "catalog_purchase",
        "description": "Hydraulic hose 3/8 high pressure",
        "mpn": "HYD-HOSE-38",
        "manufacturer": "Generic",
        "material": "rubber",
        "material_form": "hose",
        "specs": {
            "pressure_bar": 250,
            "port_size": "3/8",
            "media": "oil",
            "seal_type": "hydraulic",
        },
        "review_status": "seeded",
        "confidence": 0.93,
        "source": "seed",
        "aliases": [
            ("mpn", "HYD-HOSE-38"),
            ("description", "3/8 hydraulic hose"),
            ("description", "high pressure oil hose 3/8"),
        ],
    },
    {
        "canonical_part_key": "optical_fiber_patch_sc",
        "domain": "optical",
        "category": "optical",
        "procurement_class": "catalog_purchase",
        "description": "Fiber optic patch cable SC",
        "mpn": "OPT-FIBER-SC",
        "manufacturer": "Generic",
        "material": "polymer",
        "material_form": "cable",
        "specs": {
            "fiber_type": "single_mode",
            "connector_polish": "SC",
            "wavelength_nm": 1550,
            "core_diameter_um": 9,
        },
        "review_status": "seeded",
        "confidence": 0.92,
        "source": "seed",
        "aliases": [
            ("mpn", "OPT-FIBER-SC"),
            ("description", "fiber optic cable SC"),
            ("description", "optical patch cord SC"),
        ],
    },
    {
        "canonical_part_key": "thermal_heatsink_40mm_aluminum",
        "domain": "thermal",
        "category": "thermal",
        "procurement_class": "catalog_purchase",
        "description": "Aluminum heatsink 40mm",
        "mpn": "THERM-HS-40",
        "manufacturer": "Generic",
        "material": "aluminum",
        "material_form": "heatsink",
        "specs": {
            "fan_size_mm": 40,
            "thermal_resistance_k_per_w": 2.5,
            "heatsink_type": "extruded",
            "pad_thickness_mm": 1.5,
        },
        "review_status": "seeded",
        "confidence": 0.92,
        "source": "seed",
        "aliases": [
            ("mpn", "THERM-HS-40"),
            ("description", "40mm heatsink"),
            ("description", "aluminium heat sink 40 mm"),
        ],
    },
]
def upsert_alias(session, part_master_id: str, alias_type: str, alias_value: str, normalized_value: str):
    exists = session.query(PartAlias).filter(
        PartAlias.part_master_id == part_master_id,
        PartAlias.alias_type == alias_type,
        PartAlias.normalized_value == normalized_value,
    ).first()
    if not exists:
        session.add(PartAlias(
            part_master_id=part_master_id,
            alias_type=alias_type,
            alias_value=alias_value,
            normalized_value=normalized_value,
        ))


def upsert_attribute(session, part_master_id: str, key: str, value: Any):
    exists = session.query(PartAttribute).filter(
        PartAttribute.part_master_id == part_master_id,
        PartAttribute.attribute_key == key,
    ).first()
    if not exists:
        numeric_value = None
        try:
            numeric_value = float(value)
        except Exception:
            pass
        session.add(PartAttribute(
            part_master_id=part_master_id,
            attribute_key=key,
            attribute_value=str(value),
            numeric_value=numeric_value,
            source="seed",
            confidence=1.0,
        ))


def run():
    session = SessionLocal()
    try:
        for row in SEED_PARTS:
            key = row["canonical_part_key"]
            part = session.query(PartMaster).filter(
                PartMaster.canonical_part_key == key
            ).first()

            if not part:
                part = PartMaster(
                    canonical_part_key=key,
                    domain=row["domain"],
                    category=row["category"],
                    procurement_class=row["procurement_class"],
                    description=row["description"],
                    mpn=row["mpn"],
                    manufacturer=row["manufacturer"],
                    material=row.get("material", ""),
                    material_grade=row.get("material_grade", ""),
                    material_form=row.get("material_form", ""),
                    specs=row.get("specs", {}),
                    review_status=row.get("review_status", "seeded"),
                    confidence=row.get("confidence", 0.9),
                    source=row.get("source", "seed"),
                    observation_count=1,
                )
                session.add(part)
                session.flush()
            else:
                part.domain = row["domain"]
                part.category = row["category"]
                part.procurement_class = row["procurement_class"]
                part.description = row["description"]
                part.mpn = row["mpn"]
                part.manufacturer = row["manufacturer"]
                part.material = row.get("material", "")
                part.material_grade = row.get("material_grade", "")
                part.material_form = row.get("material_form", "")
                part.specs = row.get("specs", {})
                part.review_status = row.get("review_status", "seeded")
                part.confidence = max(float(part.confidence or 0), float(row.get("confidence", 0.9)))
                part.source = row.get("source", "seed")
                part.observation_count = max(int(part.observation_count or 0), 1)

            for alias_type, alias_value in row.get("aliases", []):
                normalized_value = alias_value.strip().lower()
                if alias_type in ("mpn", "supplier_pn"):
                    normalized_value = normalized_value.replace(" ", "").upper()
                upsert_alias(session, part.id, alias_type, alias_value, normalized_value)

            for k, v in (row.get("specs") or {}).items():
                upsert_attribute(session, part.id, k, v)

        session.commit()
        print(f"Seeded {len(SEED_PARTS)} canonical parts.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run()