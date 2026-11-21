"""Just-in-time delivery for finished accounts.

A finished account sitting in stock burns a paid month every day it waits. The
cheaper arrangement is to stock *free* accounts — which cost nothing to park —
and upgrade one only once an order is paid:

    paid order -> lock a free account -> run the normal recharge chain against
    it -> confirm the subscription -> write the credentials into the delivery.

The recharge chain is exactly the customer one (pre-check included), so the
pre-check that blocks a double upgrade protects JIT too. If the upgrade fails
the free account is released back to the pool and the order item stays
``stock_pending`` for the operator, mirroring the behaviour of any other
undeliverable line.

All of it runs in a worker thread (or Celery), so ``fulfill_order`` never blocks
the payment callback on a Playwright run.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import threading
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    FreeAccountPool,
    FreeAccountStatus,
    JitAccountJob,
    JitJobState,
    OrderDelivery,
    OrderItem,
    Product,
    RechargeState,
    RechargeTask,
    SkuType,
    utcnow,
)
from app.services import feishu
from app.services import runtime_settings as runtime
from app.services.crypto import decrypt_text, encrypt_text, mask_email
from app.services.session_parser import parse_session

logger = logging.getLogger(__name__)

# Which concrete SKU one JIT product upgrades to. Derived from the item's own
# SKU when it has one; the map is only for products that carry no SKU.
DEFAULT_JIT_SKU = SkuType.CHATGPT_PLUS_1M

LOCK_MINUTES = 10


class JitError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def available_free_accounts(db: Session, product_id: int) -> int:
    from sqlalchemy import func

    return (
        db.query(func.count(FreeAccountPool.id))
        .filter(
            FreeAccountPool.product_id == product_id,
            FreeAccountPool.status == FreeAccountStatus.AVAILABLE,
        )
        .scalar()
        or 0
    )


def claim_free_account(db: Session, product_id: int, order_item_id: int) -> FreeAccountPool:
    """Lock one free account for this order item, oldest first."""
    account = (
        db.query(FreeAccountPool)
        .filter(
            FreeAccountPool.product_id == product_id,
            FreeAccountPool.status == FreeAccountStatus.AVAILABLE,
        )
        .order_by(FreeAccountPool.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if account is None:
        raise JitError("JIT_POOL_EMPTY", "白号池暂无可用账号，订单已转为人工补货")
    account.status = FreeAccountStatus.LOCKED
    account.order_item_id = order_item_id
    account.locked_at = utcnow()
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def release_free_account(db: Session, account: FreeAccountPool) -> None:
    if account.status == FreeAccountStatus.SOLD:
        return
    account.status = FreeAccountStatus.AVAILABLE
    account.order_item_id = None
    account.locked_at = None
    db.add(account)
    db.commit()


def _pending_jobs(db: Session, order_item_id: int) -> list[JitAccountJob]:
    return (
        db.query(JitAccountJob)
        .filter(
            JitAccountJob.order_item_id == order_item_id,
            JitAccountJob.state.in_([JitJobState.PENDING, JitJobState.RECHARGING]),
        )
        .order_by(JitAccountJob.id.asc())
        .all()
    )


def start_jit_delivery(db: Session, order_item: OrderItem) -> JitAccountJob:
    """Queue upgrades for every remaining undelivered unit of one paid order item.

    Idempotent: counts existing deliveries plus in-flight jobs so multi-quantity
    orders (quantity 1..5) spawn exactly ``quantity`` upgrades without duplicates.
    """
    from sqlalchemy import func

    delivered = int(
        db.query(func.count(OrderDelivery.id))
        .filter(OrderDelivery.order_item_id == order_item.id)
        .scalar()
        or 0
    )
    active = _pending_jobs(db, order_item.id)
    needed = max(0, int(order_item.quantity or 1) - delivered - len(active))
    created: list[JitAccountJob] = []
    for _ in range(needed):
        job = JitAccountJob(order_item_id=order_item.id, state=JitJobState.PENDING)
        db.add(job)
        created.append(job)
    if created:
        db.commit()
        for job in created:
            db.refresh(job)
            dispatch_job(job.id)
        return created[0]
    if active:
        return active[0]
    # Already fully delivered; return the most recent job for caller convenience.
    latest = (
        db.query(JitAccountJob)
        .filter(JitAccountJob.order_item_id == order_item.id)
        .order_by(JitAccountJob.id.desc())
        .first()
    )
    if latest is not None:
        return latest
    job = JitAccountJob(order_item_id=order_item.id, state=JitJobState.PENDING)
    db.add(job)
    db.commit()
    db.refresh(job)
    dispatch_job(job.id)
    return job


def recover_stale_jit_jobs(db: Session) -> dict[str, int]:
    """Release leaked free-account locks and fail/requeue orphaned JIT jobs."""
    now = utcnow()
    lock_cutoff = now - timedelta(minutes=LOCK_MINUTES)
    job_cutoff = now - timedelta(minutes=max(15, LOCK_MINUTES + 5))

    released_accounts = 0
    stuck_jobs = (
        db.query(JitAccountJob)
        .filter(
            JitAccountJob.state == JitJobState.RECHARGING,
            JitAccountJob.updated_at < job_cutoff,
        )
        .all()
    )
    recovered_jobs = 0
    for job in stuck_jobs:
        account = db.get(FreeAccountPool, job.free_account_id) if job.free_account_id else None
        if account is not None and account.status == FreeAccountStatus.LOCKED:
            account.status = FreeAccountStatus.AVAILABLE
            account.order_item_id = None
            account.locked_at = None
            db.add(account)
            released_accounts += 1
        job.state = JitJobState.FAILED
        job.error_code = "JIT_WORKER_TIMEOUT"
        job.error_message = "后台升级任务超时未完成，白号已自动释放，等待重试或人工处理"
        job.completed_at = now
        db.add(job)
        recovered_jobs += 1

    # Also release any orphaned LOCKED free accounts without an active RECHARGING job.
    stale_accounts = (
        db.query(FreeAccountPool)
        .filter(
            FreeAccountPool.status == FreeAccountStatus.LOCKED,
            FreeAccountPool.locked_at.isnot(None),
            FreeAccountPool.locked_at < lock_cutoff,
        )
        .all()
    )
    for account in stale_accounts:
        active_job = (
            db.query(JitAccountJob)
            .filter(
                JitAccountJob.free_account_id == account.id,
                JitAccountJob.state == JitJobState.RECHARGING,
            )
            .first()
        )
        if active_job is None:
            account.status = FreeAccountStatus.AVAILABLE
            account.order_item_id = None
            account.locked_at = None
            db.add(account)
            released_accounts += 1

    if recovered_jobs or released_accounts:
        db.commit()
        feishu.dispatch_event(
            "system.error",
            "MOLIPLY 自动回收超时 JIT 任务与白号锁",
            {"回收任务数": recovered_jobs, "释放白号数": released_accounts},
            severity="warning",
            link_path="/admin/orders",
        )
    return {"recovered_jobs": recovered_jobs, "released_accounts": released_accounts}


def dispatch_job(job_id: int) -> None:
    mode = settings.task_mode.lower()
    if mode == "celery":
        from app.tasks.celery_worker import execute_jit_job

        execute_jit_job.delay(job_id)
    elif mode == "inline":
        threading.Thread(target=run_jit_job, args=(job_id,), daemon=True).start()


def _sku_for_item(order_item: OrderItem) -> SkuType:
    return order_item.sku_type or DEFAULT_JIT_SKU


def _session_for_account(db: Session, account: FreeAccountPool, order_item: OrderItem) -> str:
    """Session JSON parked with the account, refreshed when it went stale.

    A parked session is the cheapest path (no browser needed). If it is missing
    or expired the worker is expected to have re-extracted it; here it raises so
    the job fails visibly instead of charging with a dead credential.
    """
    raw = decrypt_text(account.session_raw_enc) if account.session_raw_enc else ""
    if not raw:
        raise JitError("JIT_SESSION_MISSING", f"白号 {mask_email(account.email)} 缺少 Session，需重新提取")
    try:
        info = parse_session(raw)
    except Exception as exc:  # noqa: BLE001 - parser codes are not all SessionError
        raise JitError("JIT_SESSION_INVALID", f"白号 {mask_email(account.email)} 的 Session 已失效") from exc
    if info.email.lower() != account.email.lower():
        raise JitError("JIT_SESSION_MISMATCH", "Session 与白号邮箱不一致，需重新提取")
    return raw


def _settle_success(db: Session, job: JitAccountJob, account: FreeAccountPool, order_item: OrderItem) -> None:
    """Deliver the credentials once the subscription is confirmed."""
    password = decrypt_text(account.password_enc)
    recovery = decrypt_text(account.recovery_enc) if account.recovery_enc else ""
    payload = f"{account.email}----{password}----{recovery}"
    db.add(
        OrderDelivery(
            order_item_id=order_item.id,
            kind="ACCOUNT_CREDENTIALS",
            account_id=None,
            payload_enc=encrypt_text(payload),
        )
    )
    account.status = FreeAccountStatus.SOLD
    account.sold_at = utcnow()
    job.state = JitJobState.DELIVERED
    job.completed_at = utcnow()
    db.add(account)
    db.add(job)
    db.commit()

    from app.services.orders import finalize_pending_order_if_delivered

    finalize_pending_order_if_delivered(db, order_item.id)
    feishu.dispatch_event(
        "order.delivered",
        "MOLIPLY JIT 成品号交付完成",
        {
            "账号": mask_email(account.email),
            "订单条目": order_item.id,
            "商品": order_item.product_name,
        },
        severity="success",
        link_path=f"/admin/orders?q={order_item.id}",
    )


def _settle_failure(db: Session, job: JitAccountJob, account: FreeAccountPool | None, code: str, message: str) -> None:
    if account is not None:
        release_free_account(db, account)
    job.state = JitJobState.FAILED
    job.error_code = code
    job.error_message = message[:500]
    job.completed_at = utcnow()
    db.add(job)
    db.commit()
    feishu.dispatch_event(
        "recharge.manual_review",
        "MOLIPLY JIT 成品号升级失败，需人工补发",
        {"订单条目": job.order_item_id, "错误码": code, "说明": message[:160]},
        severity="error",
        link_path=f"/admin/orders?q={job.order_item_id}",
    )


def run_jit_job(job_id: int) -> None:
    """Upgrade one locked free account and deliver it, or put it back."""
    db = SessionLocal()
    account: FreeAccountPool | None = None
    try:
        job = db.get(JitAccountJob, job_id)
        if job is None or job.state == JitJobState.DELIVERED:
            return
        order_item = db.get(OrderItem, job.order_item_id)
        if order_item is None:
            _settle_failure(db, job, None, "ORDER_ITEM_MISSING", "订单条目不存在")
            return
        product = db.get(Product, order_item.product_id)

        job.state = JitJobState.RECHARGING
        job.attempts += 1
        db.add(job)
        db.commit()

        if product is None:
            _settle_failure(db, job, None, "PRODUCT_MISSING", "商品不存在")
            return

        try:
            account = claim_free_account(db, product.id, order_item.id)
        except JitError as exc:
            _settle_failure(db, job, None, exc.code, exc.message)
            return
        job.free_account_id = account.id
        db.add(job)
        db.commit()

        try:
            session_raw = _session_for_account(db, account, order_item)
        except JitError as exc:
            _settle_failure(db, job, account, exc.code, exc.message)
            return

        # Reuse the customer path end to end: a trial activation code is minted
        # locally so dispatch, audit and Feishu all behave identically.
        sku = _sku_for_item(order_item)
        code = ActivationCode(
            code=f"MOLI-JIT-{uuid.uuid4().hex[:8].upper()}",
            sku_type=sku,
            channel_mode=ChannelMode.HYBRID_SELF_FIRST,
            status=CodeStatus.LOCKED,
            note=f"JIT order_item={order_item.id}",
        )
        db.add(code)
        db.flush()
        task = RechargeTask(
            task_id=str(uuid.uuid4()),
            code_id=code.id,
            target_email=account.email,
            target_user_id="",
            encrypted_session_raw=encrypt_text(session_raw),
            state=RechargeState.PENDING,
        )
        db.add(task)
        db.commit()
        job.recharge_task_id = task.task_id
        db.add(job)
        db.commit()

        from app.services import redeem as redeem_service

        redeem_service.run_recharge_task(task.task_id)

        db.expire_all()
        task = db.get(RechargeTask, task.task_id)
        code = db.get(ActivationCode, code.id)
        if task is None or task.state != RechargeState.SUCCESS:
            reason = (task.error_message if task else "") or "升级未成功"
            _settle_failure(
                db,
                job,
                account,
                (task.error_code if task else "") or "JIT_RECHARGE_FAILED",
                reason,
            )
            return
        code.status = CodeStatus.USED
        code.used_at = utcnow()
        db.add(code)
        _settle_success(db, job, account, order_item)
    except Exception as exc:  # noqa: BLE001 - a crashed job must not strand the account
        logger.exception("JIT job %s crashed", job_id)
        db.rollback()
        try:
            job = db.get(JitAccountJob, job_id)
            if job is not None and job.state != JitJobState.DELIVERED:
                account = db.get(FreeAccountPool, job.free_account_id) if job.free_account_id else None
                _settle_failure(db, job, account, "JIT_INTERNAL_ERROR", exc.__class__.__name__)
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        db.close()


def jit_enabled() -> bool:
    return runtime.get_bool("jit_fulfillment_enabled", True)


__all__ = [
    "JitError",
    "jit_enabled",
    "available_free_accounts",
    "claim_free_account",
    "dispatch_job",
    "release_free_account",
    "run_jit_job",
    "start_jit_delivery",
]
