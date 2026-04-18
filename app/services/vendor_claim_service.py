"""Vendor claim & verification workflow (Blueprint §16.1)."""
from __future__ import annotations
import secrets, hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.vendor_invite import VendorInviteToken
from app.models.vendor import Vendor

def generate_claim_token(db: Session, *, vendor_id: str, email: str,
                         purpose: str = "claim", invited_by: str | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    hash_ = hashlib.sha256(raw.encode()).hexdigest()
    tok = VendorInviteToken(vendor_id=vendor_id, email=email,
        invited_by_user_id=invited_by, token_hash=hash_, purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(tok)
    return raw

async def send_claim_email(db: Session, *, vendor, email: str, invited_by_name: str | None = None):
    raw = generate_claim_token(db, vendor_id=str(vendor.id), email=email, purpose="claim")
    public_url = getattr(settings, "PUBLIC_APP_URL", "https://app.pgihub.com")
    link = f"{public_url}/vendor/claim?token={raw}&vendor_id={vendor.id}"
    html = f"<p>Claim your PGI Hub vendor profile: <a href=\"{link}\">Click here</a></p>"
    try:
        from app.integrations.sendgrid_client import send_email
        await send_email(to=email, subject="Claim your PGI Hub vendor profile", html=html)
    except Exception:
        pass

def consume_claim_token(db: Session, *, raw_token: str, vendor_user_id: str):
    hash_ = hashlib.sha256(raw_token.encode()).hexdigest()
    tok = db.query(VendorInviteToken).filter_by(token_hash=hash_, consumed_at=None).first()
    if not tok: raise ValueError("Invalid or consumed token")
    if tok.expires_at < datetime.now(timezone.utc): raise ValueError("Token expired")
    tok.consumed_at = datetime.now(timezone.utc)
    vendor = db.query(Vendor).filter_by(id=tok.vendor_id).first()
    if vendor:
        vendor.profile_claimed_by = vendor_user_id
        if getattr(vendor, "tier", None) in (None, "GHOST"):
            vendor.tier = "BASIC"
    return tok
