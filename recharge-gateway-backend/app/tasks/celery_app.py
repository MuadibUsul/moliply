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
    task_time_limit=600,
    task_soft_time_limit=570,
    broker_connection_retry_on_startup=True,
    task_default_queue="fast",
    task_routes={
        "app.tasks.celery_worker.execute_recharge_task": {"queue": "browser"},
        "app.tasks.celery_worker.execute_jit_job": {"queue": "browser"},
        "app.tasks.celery_worker.*": {"queue": "fast"},
    },
    beat_schedule={
        "cleanup-sensitive-tokens": {
            "task": "app.tasks.celery_worker.cleanup_tokens_task",
            "schedule": crontab(minute="*"),
        },
        # Every 20s: the window is 120s, so a task is refunded at most 20s late.
        "sweep-sms-tasks": {
            "task": "app.tasks.celery_worker.sweep_sms_tasks_task",
            "schedule": 20.0,
        },
        "scan-direct-crypto-payments": {
            "task": "app.tasks.celery_worker.scan_direct_crypto_payments_task",
            "schedule": crontab(minute="*"),
        },
        "expire-unpaid-orders": {
            "task": "app.tasks.celery_worker.expire_unpaid_orders_task",
            "schedule": crontab(minute="*"),
        },
        "fulfill-backorders": {
            "task": "app.tasks.celery_worker.fulfill_backorders_task",
            "schedule": crontab(minute="*"),
        },
        "recover-stale-locks-and-tasks": {
            "task": "app.tasks.celery_worker.recover_stale_locks_and_tasks_task",
            "schedule": crontab(minute="*/2"),
        },
        "reconcile-upstream-tasks": {
            "task": "app.tasks.celery_worker.reconcile_upstream_tasks_task",
            "schedule": crontab(minute="*/2"),
        },
        "inventory-health-check": {
            "task": "app.tasks.celery_worker.inventory_health_check_task",
            "schedule": crontab(minute="*/30"),
        },
        "feishu-daily-summary": {
            "task": "app.tasks.celery_worker.feishu_daily_summary_task",
            "schedule": crontab(hour=9, minute=0),
        },
        "send-renewal-reminders": {
            "task": "app.tasks.celery_worker.send_renewal_reminders_task",
            "schedule": crontab(hour=10, minute=30),
        },
    },
)

celery_app.autodiscover_tasks(["app.tasks"])
