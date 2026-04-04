"""Durable object storage abstraction for uploads and attachments."""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("storage_service")


@dataclass
class StoredObject:
    provider: str
    storage_key: str
    public_url: Optional[str]
    sha256: str
    file_size_bytes: int


def _storage_provider() -> str:
    return os.getenv("OBJECT_STORAGE_PROVIDER", os.getenv("DRAWING_STORAGE_PROVIDER", settings.OBJECT_STORAGE_PROVIDER)).strip().lower()


def _bucket_name() -> str:
    return os.getenv("OBJECT_STORAGE_BUCKET", os.getenv("DRAWING_S3_BUCKET", settings.OBJECT_STORAGE_BUCKET)).strip()


def _region() -> str:
    return os.getenv("OBJECT_STORAGE_REGION", os.getenv("AWS_REGION", settings.OBJECT_STORAGE_REGION)).strip() or "us-east-1"


def _prefix() -> str:
    return os.getenv("OBJECT_STORAGE_PREFIX", settings.OBJECT_STORAGE_PREFIX).strip().strip("/") + "/"


def _local_root() -> Path:
    root = Path(settings.UPLOAD_DIR) / "object_storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_s3_client():
    try:
        import boto3  # type: ignore
        return boto3.client("s3", region_name=_region())
    except Exception:
        return None


def validate_storage_configuration(strict: bool = True) -> None:
    provider = _storage_provider()
    bucket = _bucket_name()
    if settings.is_production and settings.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION:
        if provider != "s3":
            raise RuntimeError(
                "Production requires durable object storage. Set OBJECT_STORAGE_PROVIDER=s3 "
                "(or DRAWING_STORAGE_PROVIDER=s3) and configure OBJECT_STORAGE_BUCKET."
            )
        if not bucket:
            raise RuntimeError("Production requires OBJECT_STORAGE_BUCKET when OBJECT_STORAGE_PROVIDER=s3.")
    if strict and provider == "s3" and not bucket:
        raise RuntimeError("OBJECT_STORAGE_PROVIDER=s3 requires OBJECT_STORAGE_BUCKET.")


def build_storage_key(scope: str, file_name: str, prefix: Optional[str] = None) -> str:
    safe_name = (file_name or "document.bin").replace("/", "_").replace("\\", "_")
    safe_scope = (scope or "general").strip().replace("/", "_").replace("\\", "_")
    unique = uuid.uuid4().hex
    base_prefix = prefix if prefix is not None else _prefix()
    return f"{base_prefix}{safe_scope}/{unique}_{safe_name}"


def save_bytes(
    file_bytes: bytes,
    file_name: str,
    scope: str,
    content_type: Optional[str] = None,
    prefix: Optional[str] = None,
) -> StoredObject:
    provider = _storage_provider()
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    file_size_bytes = len(file_bytes)
    key = build_storage_key(scope, file_name, prefix=prefix)
    bucket = _bucket_name()

    if settings.is_production and settings.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION:
        validate_storage_configuration(strict=True)

    if provider == "s3" and bucket:
        client = _get_s3_client()
        if client:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=file_bytes,
                ContentType=content_type or "application/octet-stream",
            )
            public_url = f"s3://{bucket}/{key}"
            return StoredObject(provider="s3", storage_key=key, public_url=public_url, sha256=sha256, file_size_bytes=file_size_bytes)
        if settings.is_production:
            raise RuntimeError("S3 object storage is required in production but boto3 or bucket is unavailable.")
        logger.warning("OBJECT_STORAGE_PROVIDER=s3 but boto3 or bucket is unavailable; falling back to local storage")

    if settings.is_production and settings.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION:
        raise RuntimeError("Production requires durable object storage, but the configured provider is not available.")

    local_path = _local_root() / key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(file_bytes)
    public_url = str(local_path)
    return StoredObject(provider="local", storage_key=str(local_path), public_url=public_url, sha256=sha256, file_size_bytes=file_size_bytes)


def load_bytes(provider: str, storage_key: str) -> Optional[bytes]:
    provider = (provider or "local").strip().lower()
    if not storage_key:
        return None

    if provider == "s3":
        bucket = _bucket_name()
        client = _get_s3_client()
        if not bucket or not client:
            return None
        try:
            response = client.get_object(Bucket=bucket, Key=storage_key)
            return response["Body"].read()
        except Exception as exc:
            logger.error("Failed to load S3 object %s: %s", storage_key, exc)
            return None

    path = Path(storage_key)
    if not path.exists():
        return None
    return path.read_bytes()


def delete_object(provider: str, storage_key: str) -> None:
    provider = (provider or "local").strip().lower()
    if not storage_key:
        return

    if provider == "s3":
        bucket = _bucket_name()
        client = _get_s3_client()
        if bucket and client:
            try:
                client.delete_object(Bucket=bucket, Key=storage_key)
            except Exception as exc:
                logger.warning("Failed to delete S3 object %s: %s", storage_key, exc)
        return

    path = Path(storage_key)
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        logger.warning("Failed to delete local object %s: %s", storage_key, exc)
