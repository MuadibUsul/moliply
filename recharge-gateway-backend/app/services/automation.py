"""Self-healing commercial automation loops driven by Celery Beat.

Each routine is idempotent, safe to run concurrently or manually from the admin
console, and returns a structured count summary for observability.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ActivationCode,
    AuditLog,
    CodeStatus,
    Order,
    OrderStatus,
    Product,
    RechargeState,
    RechargeTask,
    VccCard,
    VccStatus,
    utcnow,
)
from app.services import feishu
from app.services import runtime_settings as runtime
from app.services.catalog import available_account_stock, available_stock
from app.services.fulfillment import jit_delivery, vcc_pool_scheduler
from app.services.locks import redis_client
from app.services.orders import fulfill_pending_backorders

logger = logging.getLogger(__name__)

STALE_LOCK_MINUTES = 15
STALE_TASK_MINUTES = 15
DIRECT_CRYPTO_PROVIDERS = {"usdt_trc20", "usdc_erc20", "eth", "btc"}
ACTIVE_RECHARGE_STATES = [
    RechargeState.PENDING,
    RechargeState.CHECKING_ACCOUNT,
    RechargeState.CREATING_CHECKOUT,
    RechargeState.PAYING,
    RechargeState.AWAITING_3DS,
]
RISKY_RECHARGE_STATES = {
    RechargeState.CREATING_CHECKOUT,
    RechargeState.PAYING,
    RechargeState.AWAITING_3DS,
}


def expire_unpaid_orders(db: Session, *, limit: int = 200) -> int:
    """Cancel overdue PENDING orders while respecting the crypto grace window."""
    now = utcnow()
    grace = timedelta(
        minutes=max(1, runtime.get_int("crypto_payment_grace_minutes", settings.crypto_payment_grace_minutes))
    )
    candidates = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.expires_at.isnot(None),
            Order.expires_at < now,
        )
        .order_by(Order.expires_at.asc())
        .limit(limit)
        .all()
    )
    expired = 0
    for order in candidates:
        if order.payment_provider in DIRECT_CRYPTO_PROVIDERS:
            if order.expires_at and order.expires_at > now - grace:
                continue
        order.status = OrderStatus.CANCELLED
        db.add(order)
        db.add(
            AuditLog(
                actor="system",
                action="order.expired",
                entity_type="order",
                entity_id=order.order_no,
                detail=f"provider={order.payment_provider or 'none'}",
            )
        )
        expired += 1
    if expired:
        db.commit()
        logger.info("Auto-expired %s overdue unpaid order(s)", expired)
    return expired


def recover_stale_vcc_locks(db: Session, *, stale_minutes: int = STALE_LOCK_MINUTES) -> int:
    """Release VCC locks stranded by a crashed worker so cards don't stay frozen."""
    cutoff = utcnow() - timedelta(minutes=max(5, stale_minutes))
    stuck_cards = (
        db.query(VccCard)
        .filter(
            VccCard.is_locked.is_(True),
            VccCard.locked_at.isnot(None),
            VccCard.locked_at < cutoff,
        )
        .all()
    )
    recovered = 0
    for card in stuck_cards:
        # Never unlock a card that still has a recently updated in-flight task.
        active = (
            db.query(RechargeTask)
            .filter(
                RechargeTask.vcc_card_id == card.id,
                RechargeTask.state.in_(ACTIVE_RECHARGE_STATES),
                RechargeTask.updated_at >= cutoff,
            )
            .first()
        )
        if active is not None:
            continue
        card.is_locked = False
        card.locked_at = None
        card.reserved_balance = Decimal("0.00")
        card.current_bind_count = max(0, card.current_bind_count - 1)
        db.add(card)
        db.add(
            AuditLog(
                actor="system",
                action="vcc.stale_lock_recovered",
                entity_type="vcc_card",
                entity_id=str(card.id),
                detail=f"bin={card.card_bin}",
            )
        )
        recovered += 1
    if recovered:
        db.commit()
        feishu.dispatch_event(
            "system.error",
            "MOLIPLY 自动回收僵尸 VCC 卡锁",
            {"释放卡片数": recovered, "阈值(分钟)": stale_minutes},
            severity="warning",
            link_path="/admin/cards",
        )
    return recovered


def recover_stale_recharge_tasks(db: Session, *, stale_minutes: int = STALE_TASK_MINUTES) -> dict[str, int]:
    """Recover RechargeTasks left mid-flight after a worker crash or OOM."""
    now = utcnow()
    cutoff = now - timedelta(minutes=max(5, stale_minutes))
    stuck = (
        db.query(RechargeTask)
        .filter(
            RechargeTask.state.in_(ACTIVE_RECHARGE_STATES),
            RechargeTask.updated_at < cutoff,
        )
        .limit(100)
        .all()
    )
    released = 0
    manual_review = 0
    for task in stuck:
        code = db.get(ActivationCode, task.code_id)
        risky = task.state in RISKY_RECHARGE_STATES
        task.state = RechargeState.FAILED
        task.completed_at = now
        if risky:
            task.error_code = "PAYMENT_OUTCOME_UNKNOWN"
            task.error_message = "后台执行超时且已进入支付阶段，为防止重复扣款已转人工核查"
            manual_review += 1
        else:
            task.error_code = "WORKER_TIMEOUT"
            task.error_message = "后台队列执行超时，激活码已自动释放，请重新提交"
            if code is not None and code.status == CodeStatus.LOCKED:
                code.status = CodeStatus.UNUSED
                db.add(code)
            released += 1
        db.add(task)
    if stuck:
        db.commit()
        feishu.dispatch_event(
            "recharge.manual_review" if manual_review else "system.error",
            "MOLIPLY 自动回收超时充值任务",
            {"自动释放重跑": released, "转人工复核": manual_review},
            severity="warning" if not manual_review else "error",
            link_path="/admin/tasks",
        )
    return {"released": released, "manual_review": manual_review}


def reconcile_upstream_tasks(db: Session, *, limit: int = 30) -> dict[str, int]:
    """Re-poll upstream for UPSTREAM_TIMEOUT / UPSTREAM_PENDING tasks with an order_no."""
    from app.services.upstream_provider import UpstreamProvider

    provider = UpstreamProvider()
    if provider.mode != "http" or not (provider.base and provider.merchant_key):
        return {"checked": 0, "succeeded": 0, "failed": 0}

    now = utcnow()
    cutoff = now - timedelta(hours=24)
    tasks = (
        db.query(RechargeTask)
        .filter(
            RechargeTask.state == RechargeState.FAILED,
            RechargeTask.error_code.in_(["UPSTREAM_TIMEOUT", "UPSTREAM_PENDING"]),
            RechargeTask.upstream_order_no.isnot(None),
            RechargeTask.upstream_order_no != "",
            RechargeTask.created_at >= cutoff,
        )
        .order_by(RechargeTask.updated_at.asc())
        .limit(limit)
        .all()
    )
    checked = 0
    succeeded = 0
    failed = 0
    for task in tasks:
        checked += 1
        status_info = provider.check_order(task.upstream_order_no or "")
        if status_info is None:
            continue
        state_val, message = status_info
        code = db.get(ActivationCode, task.code_id)
        if state_val == "SUCCESS":
            task.state = RechargeState.SUCCESS
            task.error_code = None
            task.error_message = None
            task.completed_at = now
            if code is not None:
                code.status = CodeStatus.USED
                code.used_at = now
                db.add(code)
            db.add(task)
            db.commit()
            succeeded += 1
        elif state_val == "FAILED":
            task.error_code = "UPSTREAM_REJECTED"
            task.error_message = message or "上游确认失败，激活码已释放，请重试"
            if code is not None and code.status == CodeStatus.LOCKED:
                code.status = CodeStatus.UNUSED
                db.add(code)
            db.add(task)
            db.commit()
            failed += 1
    if succeeded or failed:
        feishu.dispatch_event(
            "order.delivered",
            "MOLIPLY 上游超时工单自动对账完成",
            {"检查数": checked, "自动确认成功": succeeded, "确认失败已释放": failed},
            severity="info",
            link_path="/admin/tasks",
        )
    return {"checked": checked, "succeeded": succeeded, "failed": failed}


def run_inventory_health_check(db: Session) -> dict:
    """Periodic low-stock check across products, JIT free pools, and VCC cards."""
    threshold = runtime.get_int("feishu_low_stock_threshold", settings.feishu_low_stock_threshold)
    low_products: list[str] = []
    if threshold > 0:
        for product in db.query(Product).filter(Product.active.is_(True)).all():
            stock = (
                available_stock(db, product)
                if product.delivery_type.value == "ACTIVATION_CODE"
                else available_account_stock(db, product)
            )
            if stock <= threshold:
                low_products.append(f"{product.name}({stock})")

    vcc_healthy = None
    if runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled) and not runtime.get_bool(
        "self_vcc_dry_run", settings.self_vcc_dry_run
    ):
        from app.services import region_router

        vcc_healthy = vcc_pool_scheduler.check_watermark(db, region_router.min_balance_for(region_router.US))

    if low_products:
        # Cooldown key prevents spamming Feishu every 30 min if unchanged.
        client = redis_client()
        dedup_key = "moliply:alert:inventory_health"
        should_send = True
        if client is not None:
            try:
                should_send = bool(client.set(dedup_key, "1", nx=True, ex=7200))
            except Exception:  # noqa: BLE001
                should_send = True
        if should_send:
            feishu.dispatch_event(
                "stock.low",
                "MOLIPLY 定时库存巡检预警",
                {
                    "低库存商品数": len(low_products),
                    "预警阈值": threshold,
                    "商品清单": "、".join(low_products[:10]),
                },
                severity="warning",
                link_path="/admin/products",
            )
    return {"low_products": low_products, "vcc_healthy": vcc_healthy}


def run_all_recovery(db: Session) -> dict:
    """Single entry point for Beat and admin manual recovery."""
    expired_orders = expire_unpaid_orders(db)
    vcc_locks = recover_stale_vcc_locks(db)
    jit_res = jit_delivery.recover_stale_jit_jobs(db)
    task_res = recover_stale_recharge_tasks(db)
    backorders = fulfill_pending_backorders(db)
    return {
        "expired_orders": expired_orders,
        "recovered_vcc_locks": vcc_locks,
        "recovered_jit": jit_res,
        "recovered_tasks": task_res,
        "fulfilled_backorders": backorders,
    }


def send_renewal_reminders(
    db: Session,
    *,
    days_since_delivery: int = 27,
    coupon_amount: Decimal = Decimal("5.00"),
    min_amount: Decimal = Decimal("30.00"),
    limit: int = 50,
) -> dict[str, int]:
    """Send 27-day renewal reminder emails with a 48h single-use comeback coupon."""
    import secrets
    from app.models import Coupon
    from app.services import mailer

    now = utcnow()
    window_end = now - timedelta(days=days_since_delivery)
    window_start = now - timedelta(days=days_since_delivery + 1)

    orders = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.DELIVERED,
            Order.delivered_at.isnot(None),
            Order.delivered_at >= window_start,
            Order.delivered_at <= window_end,
        )
        .order_by(Order.delivered_at.asc())
        .limit(limit)
        .all()
    )

    reminded = 0
    coupons_created = 0
    for order in orders:
        already_sent = (
            db.query(AuditLog.id)
            .filter(
                AuditLog.action == "renewal.reminder_sent",
                AuditLog.entity_id == order.order_no,
            )
            .first()
        )
        if already_sent:
            continue
        code = f"RENEW{secrets.token_hex(3).upper()}"
        coupon = Coupon(
            code=code,
            amount=coupon_amount,
            min_amount=min_amount,
            active=True,
            max_uses=1,
            expires_at=now + timedelta(hours=48),
        )
        db.add(coupon)
        db.add(
            AuditLog(
                actor="system",
                action="renewal.reminder_sent",
                entity_type="order",
                entity_id=order.order_no,
                detail=f"coupon={code} email={order.email}",
            )
        )
        coupons_created += 1
        if mailer.smtp_configured():
            try:
                mailer.send_operator_email(
                    f"【{settings.brand_name_en}】您的订阅即将到期，专属 ¥{coupon_amount:.0f} 续费券已送达",
                    {
                        "原订单号": order.order_no,
                        "专属续费优惠码": code,
                        "立减金额": f"¥{coupon_amount:.2f}（满 ¥{min_amount:.0f} 可用，48 小时内有效）",
                        "续费入口": settings.public_store_url,
                    },
                    to_email=order.email,
                )
            except Exception:  # noqa: BLE001
                logger.debug("Renewal email failed for %s", order.order_no, exc_info=False)
        reminded += 1

    if reminded:
        db.commit()
    return {"checked": len(orders), "reminded": reminded, "coupons_created": coupons_created}
