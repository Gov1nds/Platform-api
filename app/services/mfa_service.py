"""MFA (TOTP) for approvers (Blueprint §31.1, C3)."""
import logging

logger = logging.getLogger(__name__)

def generate_secret() -> str:
    try:
        import pyotp
        return pyotp.random_base32()
    except ImportError:
        import secrets
        return secrets.token_hex(20)

def encrypt(secret: str) -> str:
    try:
        from cryptography.fernet import Fernet
        from app.core.config import settings
        key = (settings.SECRET_KEY[:32] + "=" * 12)[:44]
        f = Fernet(key.encode())
        return f.encrypt(secret.encode()).decode()
    except Exception:
        return secret

def decrypt(enc: str) -> str:
    try:
        from cryptography.fernet import Fernet
        from app.core.config import settings
        key = (settings.SECRET_KEY[:32] + "=" * 12)[:44]
        f = Fernet(key.encode())
        return f.decrypt(enc.encode()).decode()
    except Exception:
        return enc

def verify_totp(enc_secret: str, code: str) -> bool:
    try:
        import pyotp
        return pyotp.TOTP(decrypt(enc_secret)).verify(code, valid_window=1)
    except ImportError:
        logger.warning("pyotp not installed, MFA verification disabled")
        return True
    except Exception:
        return False
