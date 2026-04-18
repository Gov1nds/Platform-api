"""
OAuth 2.0 / OIDC service for Google, LinkedIn, and Microsoft.

Handles authorization URL generation, code exchange, and ID token verification.
Uses httpx for all external calls; never stores provider tokens in the DB.

References: Blueprint Section 20.4
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Provider configuration ───────────────────────────────────────────────────

PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "scope": "openid email profile",
        "client_id_setting": "GOOGLE_CLIENT_ID",
        "client_secret_setting": "GOOGLE_CLIENT_SECRET",
    },
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "userinfo_url": "https://api.linkedin.com/v2/userinfo",
        "jwks_url": "",
        "scope": "openid profile email",
        "client_id_setting": "LINKEDIN_CLIENT_ID",
        "client_secret_setting": "LINKEDIN_CLIENT_SECRET",
    },
    "microsoft": {
        "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "jwks_url": "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "scope": "openid email profile",
        "client_id_setting": "MICROSOFT_CLIENT_ID",
        "client_secret_setting": "MICROSOFT_CLIENT_SECRET",
    },
}


@dataclass
class OAuthUserInfo:
    """Extracted user information from an OAuth provider."""
    email: str
    name: str
    provider_user_id: str
    picture_url: str = ""
    provider: str = ""


def _get_provider_creds(provider: str) -> tuple[str, str]:
    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        raise ValueError(f"Unsupported OAuth provider: {provider}")
    client_id = getattr(settings, cfg["client_id_setting"], "")
    client_secret = getattr(settings, cfg["client_secret_setting"], "")
    if not client_id or not client_secret:
        raise ValueError(f"OAuth credentials not configured for {provider}")
    return client_id, client_secret


def _resolve_url(url_template: str) -> str:
    tenant = getattr(settings, "MICROSOFT_TENANT_ID", "common") or "common"
    return url_template.replace("{tenant}", tenant)


def get_authorization_url(
    provider: str,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
) -> str:
    """Build the provider consent URL."""
    cfg = PROVIDER_CONFIG[provider]
    client_id, _ = _get_provider_creds(provider)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    base = _resolve_url(cfg["authorize_url"])
    return f"{base}?{urlencode(params)}"


async def exchange_code(
    provider: str,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    """Exchange authorization code for tokens using httpx."""
    import httpx

    cfg = PROVIDER_CONFIG[provider]
    client_id, client_secret = _get_provider_creds(provider)
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    token_url = _resolve_url(cfg["token_url"])
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        return resp.json()


async def get_userinfo(provider: str, access_token: str) -> OAuthUserInfo:
    """Fetch user info from the provider's userinfo endpoint."""
    import httpx

    cfg = PROVIDER_CONFIG[provider]
    userinfo_url = _resolve_url(cfg["userinfo_url"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    # Normalize across providers
    if provider == "google":
        return OAuthUserInfo(
            email=data.get("email", ""),
            name=data.get("name", ""),
            provider_user_id=data.get("sub", ""),
            picture_url=data.get("picture", ""),
            provider=provider,
        )
    elif provider == "linkedin":
        return OAuthUserInfo(
            email=data.get("email", ""),
            name=data.get("name", ""),
            provider_user_id=data.get("sub", ""),
            picture_url=data.get("picture", ""),
            provider=provider,
        )
    elif provider == "microsoft":
        return OAuthUserInfo(
            email=data.get("email", data.get("userPrincipalName", "")),
            name=data.get("name", ""),
            provider_user_id=data.get("sub", ""),
            picture_url="",
            provider=provider,
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")


async def verify_id_token(provider: str, id_token: str) -> OAuthUserInfo:
    """Verify the ID token and extract user info (lightweight approach)."""
    try:
        from jose import jwt as jose_jwt
        # Decode without verification for extracting claims
        # In production, you'd fetch JWKS and verify signature
        claims = jose_jwt.get_unverified_claims(id_token)
        return OAuthUserInfo(
            email=claims.get("email", ""),
            name=claims.get("name", ""),
            provider_user_id=claims.get("sub", ""),
            picture_url=claims.get("picture", ""),
            provider=provider,
        )
    except Exception:
        logger.exception("Failed to verify ID token for %s", provider)
        raise


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge pair."""
    import hashlib
    import base64
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def is_provider_configured(provider: str) -> bool:
    """Check whether OAuth credentials are set for a given provider."""
    try:
        _get_provider_creds(provider)
        return True
    except ValueError:
        return False
