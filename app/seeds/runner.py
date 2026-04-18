from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine, init_db
from app.seeds.base import SeedStats, create_seed_tables
from app.seeds.loaders.market import load_market
from app.seeds.loaders.platform import load_platform
from app.seeds.loaders.reference import load_reference
from app.seeds.loaders.vendors import load_vendors
from app.seeds.loaders.part_master import load_part_master

logger = logging.getLogger(__name__)


class SeedRunner:
    def __init__(self, seed_root: str | Path):
        self.seed_root = Path(seed_root).resolve()
        if not self.seed_root.exists():
            raise FileNotFoundError(f"Seed folder does not exist: {self.seed_root}")

    def run(self) -> list[SeedStats]:
        init_db()
        create_seed_tables(engine)
        results: list[SeedStats] = []

        with SessionLocal() as db:
            db: Session
            try:
                for loader in (
                    load_platform,
                    load_reference,
                    load_vendors,
                    load_market,
                    load_part_master,
                ):
                    for stat in loader(self.seed_root, db):
                        results.append(stat)
                    db.commit()
            except Exception:
                db.rollback()
                logger.exception("Seed run failed; transaction rolled back")
                raise

        return results
