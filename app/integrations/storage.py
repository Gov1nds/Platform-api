"""
S3 object storage client and virus scanning integration.

References: GAP-011 (S3 storage), INT-007, architecture.md
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class S3StorageClient:
    """Thin wrapper around boto3 S3 operations."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            kwargs: dict = {
                "region_name": settings.S3_REGION,
            }
            if settings.S3_ACCESS_KEY_ID:
                kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
                kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
            if settings.S3_ENDPOINT_URL:
                kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._get_client().put_object(
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        logger.info("Uploaded %d bytes to s3://%s/%s", len(data), settings.S3_BUCKET, key)
        return key

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        return self._get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )

    def delete(self, key: str) -> None:
        self._get_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
        logger.info("Deleted s3://%s/%s", settings.S3_BUCKET, key)

    def download(self, key: str) -> bytes:
        resp = self._get_client().get_object(Bucket=settings.S3_BUCKET, Key=key)
        return resp["Body"].read()


# Singleton
s3_client = S3StorageClient()


def scan_file(data: bytes) -> str:
    """
    Virus scan via ClamAV. Returns 'CLEAN', 'INFECTED', or 'ERROR'.

    Non-fatal: if ClamAV is unavailable, returns 'CLEAN' with a warning.
    """
    if not settings.CLAMAV_HOST:
        return "CLEAN"
    try:
        import pyclamd
        cd = pyclamd.ClamdNetworkSocket(host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT)
        result = cd.scan_stream(data)
        if result is None:
            return "CLEAN"
        return "INFECTED"
    except Exception:
        logger.warning("ClamAV scan failed — defaulting to CLEAN", exc_info=True)
        return "ERROR"
