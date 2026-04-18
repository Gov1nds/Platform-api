"""One-shot script to compute embeddings for all part_master rows.
Usage: python -m app.scripts.rebuild_part_master_embeddings
"""
import asyncio, json, httpx
from sqlalchemy import text
from app.core.database import SessionLocal
from app.core.config import settings

async def main():
    total = 0
    async with httpx.AsyncClient(timeout=60.0) as c:
        while True:
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT part_id, canonical_name, spec_template "
                    "FROM part_master WHERE embedding IS NULL LIMIT 512")).fetchall()
                if not rows: break
                for batch in [rows[i:i+32] for i in range(0, len(rows), 32)]:
                    texts = [f"{r.canonical_name} | {json.dumps(r.spec_template or {})}" for r in batch]
                    r = await c.post(f"{settings.BOM_ANALYZER_URL}/api/embed",
                        json={"texts": texts},
                        headers={"X-Internal-Key": settings.INTERNAL_API_KEY})
                    r.raise_for_status()
                    vectors = r.json()["vectors"]
                    for row, vec in zip(batch, vectors):
                        db.execute(text("UPDATE part_master SET embedding = :v WHERE part_id = :id"),
                            {"v": vec, "id": row.part_id})
                    total += len(batch)
                db.commit()
    print(f"Embedded {total} parts")

if __name__ == "__main__":
    asyncio.run(main())
