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
@celery_app.task(name="app.tasks.celery_worker.feishu_daily_summary_task")
def feishu_daily_summary_task() -> dict:
    """09:00 Asia/Shanghai business summary for the Feishu bot."""
    from datetime import timedelta
    from sqlalchemy import func
    from app.models import (
        ActivationCode,
        CodeStatus,
        Order,
        OrderStatus,
        RechargeState,
        RechargeTask,
        utcnow,
    )
    from app.services import feishu

    db = SessionLocal()
    try:
        now = utcnow()
        local_now = now + timedelta(hours=8)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = local_start - timedelta(hours=8)

        orders_today = db.query(func.count(Order.id)).filter(Order.created_at >= start_utc).scalar() or 0
        revenue_today = (
            db.query(func.coalesce(func.sum(Order.amount), 0))
            .filter(
                Order.paid_at >= start_utc,
                Order.status.in_([OrderStatus.PAID, OrderStatus.DELIVERED]),
            )
            .scalar()
            or 0
        )
        delivered_today = (
            db.query(func.count(Order.id))
            .filter(Order.delivered_at >= start_utc, Order.status == OrderStatus.DELIVERED)
            .scalar()
            or 0
        )
        pending_orders = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.PENDING).scalar() or 0
        tasks_success = (
            db.query(func.count(RechargeTask.task_id))
            .filter(RechargeTask.state == RechargeState.SUCCESS, RechargeTask.completed_at >= start_utc)
            .scalar()
            or 0
        )
        tasks_failed = (
            db.query(func.count(RechargeTask.task_id))
            .filter(RechargeTask.state == RechargeState.FAILED, RechargeTask.completed_at >= start_utc)
            .scalar()
            or 0
        )
        manual_review = (
            db.query(func.count(RechargeTask.task_id))
            .join(ActivationCode, ActivationCode.id == RechargeTask.code_id)
            .filter(RechargeTask.state == RechargeState.FAILED, ActivationCode.status == CodeStatus.LOCKED)
            .scalar()
            or 0
        )
        result = feishu.send_feishu(
            "daily.summary",
            "MOLIPLY 每日经营日报",
            {
                "日期": local_now.strftime("%Y-%m-%d"),
                "新建订单": int(orders_today),
                "今日收入": f"¥{float(revenue_today):.2f}",
                "今日交付": int(delivered_today),
                "待支付订单": int(pending_orders),
                "充值成功": int(tasks_success),
                "充值失败": int(tasks_failed),
                "待人工复核": int(manual_review),
            },
            severity="info",
            link_path="/admin",
        )
        logger.info("Feishu daily summary result: %s", result)
        return result
    finally:
        db.close()
