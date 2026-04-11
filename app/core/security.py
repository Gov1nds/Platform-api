"""
Security primitives: JWT (RS256 + HS256 fallback), password hashing,
token payload types.

References: GAP-008 (RS256, 15-min access, 7-day refresh),
            GAP-032 (eliminate default secret), architecture.md CC-02
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Key loading ──────────────────────────────────────────────────────────────

_private_key: bytes | str = settings.SECRET_KEY
_public_key: bytes | str = settings.SECRET_KEY
_active_algorithm: str = "HS256"

if settings.JWT_ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY_PATH:
    try:
        _private_key = Path(settings.JWT_PRIVATE_KEY_PATH).read_bytes()
        _public_key = Path(settings.JWT_PUBLIC_KEY_PATH).read_bytes()
        _active_algorithm = "RS256"
        logger.info("Loaded RS256 key pair for JWT signing")
    except FileNotFoundError:
        if settings.is_production:
            raise RuntimeError("RS256 key files not found — cannot start in production")
        logger.warning("RS256 key files not found; falling back to HS256 with SECRET_KEY")


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class TokenPayload:
    sub: str
    email: str = ""
    role: str = ""
    type: str = "access"  # access | refresh | guest
    organization_id: str | None = None
    vendor_id: str | None = None
    exp: datetime | None = None
    iat: datetime | None = None


@dataclass
class RefreshPayload:
    sub: str
    type: str = "refresh"
    exp: datetime | None = None


# ── Password utilities ───────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── Token creation ───────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create a short-lived access token (default 15 min).

    ``data`` must contain at minimum ``sub`` (user/vendor ID).
    Optionally include ``email``, ``role``, ``organization_id``, ``vendor_id``.
    """
    now = datetime.now(timezone.utc)
    to_encode = data.copy()
    to_encode["exp"] = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["iat"] = now
    to_encode["type"] = "access"
    return jwt.encode(to_encode, _private_key, algorithm=_active_algorithm)


def create_refresh_token(data: dict) -> str:
    """Create a long-lived refresh token (default 7 days)."""
    now = datetime.now(timezone.utc)
    to_encode = {
        "sub": data["sub"],
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(to_encode, _private_key, algorithm=_active_algorithm)


def create_guest_session_token() -> str:
    """Create a token value for HttpOnly guest session cookie."""
    return str(uuid.uuid4())


# ── Token decoding ───────────────────────────────────────────────────────────

def decode_access_token(token: str) -> TokenPayload | None:
    """Decode and validate an access token. Returns None on any failure."""
    payload = _decode_raw(token)
    if payload is None:
        return None
    if payload.get("type") not in ("access", None):
        # None for legacy tokens that lack a type claim
        return None
    return TokenPayload(
        sub=payload.get("sub", ""),
        email=payload.get("email", ""),
        role=payload.get("role", ""),
        type=payload.get("type", "access"),
        organization_id=payload.get("organization_id"),
        vendor_id=payload.get("vendor_id"),
        exp=payload.get("exp"),
        iat=payload.get("iat"),
    )


def decode_refresh_token(token: str) -> RefreshPayload | None:
    """Decode and validate a refresh token."""
    payload = _decode_raw(token)
    if payload is None or payload.get("type") != "refresh":
        return None
    return RefreshPayload(
        sub=payload.get("sub", ""),
        type="refresh",
        exp=payload.get("exp"),
    )


def decode_token(token: str) -> dict | None:
    """
    Legacy decode — returns raw dict.

    Kept for backward compatibility with existing routes/dependencies
    during the migration period.
    """
    return _decode_raw(token)


# ── Internal ─────────────────────────────────────────────────────────────────

def _decode_raw(token: str) -> dict | None:
    """Try RS256, then HS256 fallback if enabled."""
    # Primary algorithm
    try:
        return jwt.decode(token, _public_key, algorithms=[_active_algorithm])
    except JWTError:
        pass

    # HS256 fallback for tokens issued before RS256 migration
    if settings.LEGACY_HS256_ENABLED and _active_algorithm != "HS256":
        try:
            return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except JWTError:
            pass

    return None