from __future__ import annotations

import logging

from app.database import SessionLocal
from app.services import redeem as redeem_service
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.celery_worker.execute_recharge_task", bind=True)
def execute_recharge_task(self, task_id: str) -> None:
    logger.info("Celery picked up recharge task %s", task_id)
    redeem_service.run_recharge_task(task_id)


@celery_app.task(name="app.tasks.celery_worker.cleanup_tokens_task")
def cleanup_tokens_task() -> int:
    db = SessionLocal()
    try:
        cleared = redeem_service.cleanup_sensitive_tokens(db)
        if cleared:
            logger.info("Cleared %s expired session credentials", cleared)
        return cleared
    finally:
        db.close()
