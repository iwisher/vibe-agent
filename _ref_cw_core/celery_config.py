"""Celery configuration for async task execution."""

import os
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure

# Celery app configuration
celery_app = Celery(
    "claudeworker",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
    include=["claudeworker.core.executor"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


def get_celery_app():
    """Get the Celery app instance."""
    return celery_app
