"""Tariff ingestion from bulk datasets (Blueprint §24.2, §29.3)."""
from __future__ import annotations
import csv, io, logging
import httpx
from datetime import date
from app.core.config import settings

logger = logging.getLogger(__name__)

TARIFF_SOURCES = {
    "cbp_usa": "https://hts.usitc.gov/reststop/exportList?from=0000&to=9999&format=CSV",
}

class TariffClient:
    async def load_cbp_usa(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(TARIFF_SOURCES["cbp_usa"])
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            return [{"hs_code": r.get("HTS Number","").replace(".", ""),
                     "from_country": "ANY", "to_country": "US",
                     "duty_rate_pct": _parse_cbp_rate(r.get("General Rate of Duty", "")),
                     "effective_date": date.today().isoformat(), "source": "cbp_usa"}
                    for r in rows if r.get("HTS Number")]

    async def load_fta_routes(self) -> list[dict]:
        from pathlib import Path
        p = Path("seed/reference/fta_agreements.yaml")
        if not p.exists(): return []
        try:
            import yaml
            return yaml.safe_load(p.read_text()) or []
        except ImportError:
            return []

def _parse_cbp_rate(s):
    s = (s or "").strip().replace("%", "").replace("Free", "0")
    try: return float(s)
    except: return 0.0
