"""ClamAV virus scanning (Blueprint §31.2)."""
from fastapi import HTTPException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
_client = None

def _get_client():
    global _client
    if _client is None:
        try:
            import pyclamd
            _client = pyclamd.ClamdNetworkSocket(host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT)
            if not _client.ping():
                _client = None
        except Exception:
            _client = None
    return _client

def scan_bytes(data: bytes) -> None:
    """Raise HTTPException(400) on infected; silent pass if ClamAV unavailable."""
    c = _get_client()
    if c is None:
        return
    result = c.scan_stream(data)
    if result:
        raise HTTPException(400, f"Virus detected: {list(result.values())[0]}")
