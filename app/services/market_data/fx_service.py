"""Forex locking on quote submission (Blueprint §12.1, §23.2, C18)."""
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

def get_locked_fx(db: Session, from_currency: str, to_currency: str, quote_id: str) -> dict:
    if from_currency == to_currency:
        return {"rate": 1.0, "fx_rate_id": None, "locked_at": datetime.now(timezone.utc).isoformat()}
    row = db.execute(text(
        "SELECT id, rate, fetched_at FROM fx_rates "
        "WHERE from_currency = :f AND to_currency = :t AND freshness_status = 'FRESH' "
        "ORDER BY fetched_at DESC LIMIT 1"), {"f": from_currency, "t": to_currency}).first()
    if not row:
        row = db.execute(text(
            "SELECT id, rate, fetched_at FROM fx_rates "
            "WHERE from_currency = :f AND to_currency = :t "
            "ORDER BY fetched_at DESC LIMIT 1"), {"f": from_currency, "t": to_currency}).first()
        if not row:
            raise ValueError(f"No FX rate for {from_currency}->{to_currency}")
    db.execute(text("UPDATE fx_rates SET locked_for_quote_id = :qid "
                    "WHERE id = :rid AND locked_for_quote_id IS NULL"),
               {"qid": quote_id, "rid": row.id})
    return {"rate": float(row.rate), "fx_rate_id": str(row.id),
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "source_fetched_at": row.fetched_at.isoformat()}


# ── Service singleton (backward compatibility) ─────────────────────────────

class FXService:
    """Forex conversion and locking service."""
    
    def convert(self, amount: float, from_currency: str, to_currency: str,
                db=None) -> dict:
        """Convert amount between currencies using latest rate."""
        if from_currency == to_currency:
            return {"converted": amount, "rate": 1.0, "from": from_currency, "to": to_currency}
        if db is None:
            from app.core.database import SessionLocal
            with SessionLocal() as db:
                return self._convert(db, amount, from_currency, to_currency)
        return self._convert(db, amount, from_currency, to_currency)

    def _convert(self, db, amount, from_currency, to_currency):
        row = db.execute(text(
            "SELECT rate FROM fx_rates "
            "WHERE from_currency = :f AND to_currency = :t "
            "ORDER BY fetched_at DESC LIMIT 1"
        ), {"f": from_currency, "t": to_currency}).first()
        if row:
            rate = float(row.rate)
            return {"converted": amount * rate, "rate": rate,
                    "from": from_currency, "to": to_currency}
        # Try inverse
        row = db.execute(text(
            "SELECT rate FROM fx_rates "
            "WHERE from_currency = :t AND to_currency = :f "
            "ORDER BY fetched_at DESC LIMIT 1"
        ), {"f": from_currency, "t": to_currency}).first()
        if row:
            rate = 1.0 / float(row.rate)
            return {"converted": amount * rate, "rate": rate,
                    "from": from_currency, "to": to_currency}
        return {"converted": amount, "rate": 1.0, "from": from_currency, "to": to_currency,
                "warning": "No rate found, using 1:1"}

    def lock_for_quote(self, db, from_currency: str, to_currency: str, quote_id: str) -> dict:
        return get_locked_fx(db, from_currency, to_currency, quote_id)

fx_service = FXService()
