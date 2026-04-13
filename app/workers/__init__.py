"""
Celery application and task autodiscovery.

References: Background job infrastructure for all async processing.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from celery import Celery

    celery_app = Celery(
        "pgi-platform",
        broker=settings.REDIS_URL,
        backend=settings.REDIS_URL,
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )
    celery_app.autodiscover_tasks(["app.workers"])
    logger.info("Celery app configured with broker=%s", settings.REDIS_URL.split("@")[-1])
except ImportError:
    celery_app = None  # type: ignore[assignment]
    logger.warning("Celery not installed — background tasks unavailable")
