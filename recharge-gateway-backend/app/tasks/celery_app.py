from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "moliply",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=270,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "cleanup-sensitive-tokens": {
            "task": "app.tasks.celery_worker.cleanup_tokens_task",
            "schedule": crontab(minute="*"),
        }
    },
)

celery_app.autodiscover_tasks(["app.tasks"])
