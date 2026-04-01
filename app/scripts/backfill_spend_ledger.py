"""Backfill spend ledger and analytics rollups from existing operational data."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.services.analytics_service import backfill_spend_ledger

def run(reset: bool = True):
    db = SessionLocal()
    try:
        backfill_spend_ledger(db, reset=reset)
        db.commit()
        print("Spend ledger backfill complete.")
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run(reset=True)