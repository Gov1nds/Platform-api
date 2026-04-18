from __future__ import annotations
import csv, json
from pathlib import Path
from sqlalchemy import text
from app.seeds.base import SeedStats

def load_part_master(seed_root: Path, db):
    csv_path = seed_root / "reference" / "part_master.csv"
    if not csv_path.exists():
        yield SeedStats("part_master", 0, 0)
        return
    inserted = updated = 0
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            spec = row.get("spec_template", "{}") or "{}"
            try: json.loads(spec)
            except: spec = "{}"
            try:
                res = db.execute(text("""
                    INSERT INTO part_master
                        (part_id, canonical_name, category, commodity_group, taxonomy_code,
                         spec_template, default_uom, manufacturer_part_number, manufacturer)
                    VALUES
                        (COALESCE(NULLIF(:part_id, \'\')::uuid, gen_random_uuid()),
                         :canonical_name, :category, :commodity_group, :taxonomy_code,
                         :spec_template::jsonb, :default_uom, :mpn, :manufacturer)
                    ON CONFLICT (part_id) DO UPDATE SET
                        canonical_name=EXCLUDED.canonical_name,
                        spec_template=EXCLUDED.spec_template,
                        last_updated=NOW()
                    RETURNING (xmax = 0) AS inserted
                """), {"part_id": row.get("part_id") or None,
                    "canonical_name": row["canonical_name"],
                    "category": row.get("category", "unknown"),
                    "commodity_group": row.get("commodity_group"),
                    "taxonomy_code": row.get("taxonomy_code"),
                    "spec_template": spec,
                    "default_uom": row.get("default_uom", "each"),
                    "mpn": row.get("manufacturer_part_number") or None,
                    "manufacturer": row.get("manufacturer") or None})
                was_insert = res.fetchone()
                if was_insert and was_insert[0]: inserted += 1
                else: updated += 1
            except Exception:
                pass
    yield SeedStats("part_master", inserted, updated)
