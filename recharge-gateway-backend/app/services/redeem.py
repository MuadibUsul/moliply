"""Activation-code redemption, account pre-check and recharge state machine."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    ActivationCode,
    AuditLog,
    CodeStatus,
    OrderItem,
    RechargeState,
    RechargeTask,
    SkuType,
    utcnow,
)
from app.security import failure_tracker
from app.services.code_generator import is_valid_code_format, normalize_code
from app.services.crypto import decrypt_text, encrypt_text, mask_email
from app.services.errors import RechargeError
from app.services.locks import redis_lock
from app.services.self_vcc_worker import SelfVccFailure, run_self_vcc_recharge
from app.services.session_parser import (
    AccountCheck,
    SessionError,
    SessionInfo,
    check_account,
    parse_session,
)
from app.services.upstream_provider import UpstreamProvider

logger = logging.getLogger(__name__)

TERMINAL_STATES = {RechargeState.SUCCESS, RechargeState.FAILED}
REDEEMABLE_SKUS = {
    SkuType.CHATGPT_PLUS_1M,
    SkuType.CHATGPT_PLUS_3M,
    SkuType.CHATGPT_PLUS_12M,
    SkuType.CLAUDE_PRO_1M,
    SkuType.CHATGPT_PRO_200,
}
CLAUDE_SKUS = {SkuType.CLAUDE_PRO_1M}

CODE_STATUS_LABELS = {
    "UNUSED": "未使用",
    "LOCKED": "充能执行中",
    "USED": "已核销",
    "DISABLED": "已作废",
}

STATE_PROGRESS = {
    RechargeState.PENDING: 12,
    RechargeState.CHECKING_ACCOUNT: 24,
    RechargeState.CREATING_CHECKOUT: 42,
    RechargeState.PAYING: 66,
    RechargeState.AWAITING_3DS: 82,
    RechargeState.SUCCESS: 100,
    RechargeState.FAILED: 100,
}

STATE_STEP_INDEX = {
    RechargeState.PENDING: 1,
    RechargeState.CHECKING_ACCOUNT: 0,
    RechargeState.CREATING_CHECKOUT: 2,
    RechargeState.PAYING: 2,
    RechargeState.AWAITING_3DS: 3,
    RechargeState.SUCCESS: 4,
    RechargeState.FAILED: 3,
}

STATE_MESSAGES = {
    RechargeState.PENDING: "MOLIPLY 充能引擎已接收任务，正在排队执行...",
    RechargeState.CHECKING_ACCOUNT: "正在核验激活码与账号订阅状态...",
    RechargeState.CREATING_CHECKOUT: "正在创建安全支付会话...",
    RechargeState.PAYING: "MOLIPLY 充能引擎正在提交安全支付请求...",
    RechargeState.AWAITING_3DS: "支付已提交，正在等待银行 3DS 验证...",
    RechargeState.SUCCESS: "充值成功，会员权益已到账",
    RechargeState.FAILED: "充值任务未完成",
}

STEP_DEFINITIONS = [
    ("check", "验证激活码与订阅状态"),
    ("create", "创建充值任务"),
    ("pay", "提交支付请求"),
    ("verify", "等待验证和支付结果"),
    ("confirm", "确认订阅升级"),
]


class RedeemError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _expected_session_kind(sku_type: SkuType) -> str:
    return "claude" if sku_type in CLAUDE_SKUS else "chatgpt"


def _sku_label(db: Session, code: ActivationCode) -> str:
    from app.models import SKU_LABELS

    if code.order_item_id:
        item = db.get(OrderItem, code.order_item_id)
        if item:
            return f"{item.product_name} · {item.spec_label}".strip(" ·")
    return SKU_LABELS.get(code.sku_type.value, code.sku_type.value)


def _product_name(db: Session, code: ActivationCode) -> str:
    if code.order_item_id:
        item = db.get(OrderItem, code.order_item_id)
        if item:
            return item.product_name
    return _sku_label(db, code)


def _load_code(db: Session, raw_code: str, ip: str) -> tuple[str, ActivationCode]:
    normalized = normalize_code(raw_code)
    if failure_tracker.is_blocked(ip):
        raise RedeemError(
            "IP_BLOCKED",
            "尝试次数过多，当前 IP 已被临时限制，请 30 分钟后再试",
            429,
        )
    if not is_valid_code_format(normalized):
        failure_tracker.register_failure(ip)
        raise RedeemError("INVALID_CODE_FORMAT", "激活码格式不正确，请检查后重新输入")
    code = db.query(ActivationCode).filter(ActivationCode.code == normalized).first()
    if not code:
        failure_tracker.register_failure(ip)
        raise RedeemError("INVALID_CODE", "激活码不存在，请核对订单中的卡密", 404)
    failure_tracker.clear(ip)
    return normalized, code


def verify_code(db: Session, raw_code: str, ip: str) -> dict:
    normalized, code = _load_code(db, raw_code, ip)
    expected_kind = _expected_session_kind(code.sku_type)
    return {
        "valid": True,
        "code": normalized,
        "sku_type": code.sku_type.value,
        "sku_label": _sku_label(db, code),
        "product_name": _product_name(db, code),
        "status": code.status.value,
        "status_label": CODE_STATUS_LABELS.get(code.status.value, code.status.value),
        "bound_email_masked": mask_email(code.bound_account_email) if code.bound_account_email else None,
        "session_kind": expected_kind,
        "redeemable": code.status == CodeStatus.UNUSED,
        "channel_mode": code.channel_mode.value,
    }


def _prepare_session(code: ActivationCode, session: SessionInfo) -> AccountCheck:
    expected_kind = _expected_session_kind(code.sku_type)
    if session.kind != expected_kind:
        raise RedeemError(
            "SESSION_TYPE_MISMATCH",
            "Session 类型与商品不匹配，请按页面指引重新复制",
            409,
        )
    if code.bound_account_email and code.bound_account_email.lower() != session.email.lower():
        raise RedeemError(
            "CODE_BOUND_TO_OTHER_ACCOUNT",
            "该激活码已绑定其他账号，无法为当前账号充值",
            409,
        )
    return check_account(session)


def parse_and_precheck(db: Session, raw_code: str, session_raw: str, ip: str) -> dict:
    _, code = _load_code(db, raw_code, ip)
    if code.sku_type not in REDEEMABLE_SKUS:
        raise RedeemError("SKU_NOT_REDEEMABLE", "该商品无需在线核销，请查看订单中的交付内容", 409)
    if code.status == CodeStatus.USED:
        raise RedeemError("CODE_ALREADY_USED", "该激活码已完成核销", 409)
    if code.status == CodeStatus.DISABLED:
        raise RedeemError("CODE_DISABLED", "该激活码已作废，请联系客服", 409)
    if code.status == CodeStatus.LOCKED:
        raise RedeemError("CODE_IN_USE", "该激活码正在充能中，请勿重复提交", 409)

    try:
        session = parse_session(session_raw)
    except SessionError as exc:
        raise RedeemError(exc.code, exc.message, exc.status_code) from exc

    check = _prepare_session(code, session)
    if not check.eligible:
        raise RedeemError(
            check.error_code or "ACCOUNT_NOT_ELIGIBLE",
            check.message,
            409,
        )
    return {
        "eligible": True,
        "account_email": session.masked_email,
        "current_plan": check.current_plan,
        "message": check.message,
        "warning": check.warning,
        "session_kind": session.kind,
        "expires_at": session.expires_at.isoformat() + "Z",
    }


def _active_task(db: Session, code_id: int) -> RechargeTask | None:
    return (
        db.query(RechargeTask)
        .filter(RechargeTask.code_id == code_id, RechargeTask.state.notin_(list(TERMINAL_STATES)))
        .order_by(RechargeTask.created_at.desc())
        .first()
    )


def submit_recharge(db: Session, raw_code: str, session_raw: str, ip: str) -> RechargeTask:
    normalized, code = _load_code(db, raw_code, ip)
    if code.status == CodeStatus.USED:
        raise RedeemError("CODE_ALREADY_USED", "该激活码已完成核销", 409)
    if code.status == CodeStatus.DISABLED:
        raise RedeemError("CODE_DISABLED", "该激活码已作废，请联系客服", 409)

    existing = _active_task(db, code.id) if code.status == CodeStatus.LOCKED else None
    if existing:
        return existing
    if code.status == CodeStatus.LOCKED:
        raise RedeemError("CODE_IN_USE", "该激活码正在充能中，请勿重复提交", 409)
    if code.sku_type not in REDEEMABLE_SKUS:
        raise RedeemError("SKU_NOT_REDEEMABLE", "该商品无需在线核销", 409)

    try:
        session = parse_session(session_raw)
    except SessionError as exc:
        raise RedeemError(exc.code, exc.message, exc.status_code) from exc
    check = _prepare_session(code, session)
    if not check.eligible:
        raise RedeemError(check.error_code or "ACCOUNT_NOT_ELIGIBLE", check.message, 409)

    with redis_lock(f"lock:redeem:{normalized}") as acquired:
        if not acquired:
            raise RedeemError("CODE_IN_USE", "该激活码正在核销中，请勿重复提交", 409)
        code = (
            db.query(ActivationCode)
            .filter(ActivationCode.code == normalized)
            .with_for_update()
            .first()
        )
        if not code:
            raise RedeemError("INVALID_CODE", "激活码不存在", 404)
        if code.status == CodeStatus.LOCKED:
            existing = _active_task(db, code.id)
            if existing:
                return existing
            raise RedeemError("CODE_IN_USE", "该激活码正在核销中，请勿重复提交", 409)
        if code.status != CodeStatus.UNUSED:
            raise RedeemError("CODE_NOT_AVAILABLE", "该激活码当前不可用", 409)
        if code.bound_account_email and code.bound_account_email.lower() != session.email.lower():
            raise RedeemError("CODE_BOUND_TO_OTHER_ACCOUNT", "该激活码已绑定其他账号", 409)

        code.bound_account_email = code.bound_account_email or session.email
        code.status = CodeStatus.LOCKED
        task = RechargeTask(
            task_id=str(uuid.uuid4()),
            code_id=code.id,
            target_email=session.email,
            target_user_id=session.user_id,
            encrypted_access_token=encrypt_text(session.access_token),
            encrypted_session_raw=encrypt_text(session.raw),
            session_kind=session.kind,
            state=RechargeState.PENDING,
            client_ip=ip,
        )
        db.add(code)
        db.add(task)
        db.add(
            AuditLog(
                actor="customer",
                action="redeem.submit",
                entity_type="activation_code",
                entity_id=str(code.id),
                detail=f"sku={code.sku_type.value} task={task.task_id}",
                ip=ip,
            )
        )
        db.commit()
        db.refresh(task)

    dispatch_recharge(task.task_id)
    return task


def dispatch_recharge(task_id: str) -> None:
    mode = settings.task_mode.lower()
    if mode == "celery":
        from app.tasks.celery_worker import execute_recharge_task

        execute_recharge_task.delay(task_id)
    elif mode == "inline":
        threading.Thread(target=run_recharge_task, args=(task_id,), daemon=True).start()
    # manual mode intentionally leaves the task queued for tests/admin.


def _set_state(db: Session, task: RechargeTask, state: RechargeState) -> None:
    task.state = state
    task.updated_at = utcnow()
    db.add(task)
    db.commit()


def _finalize_success(db: Session, task: RechargeTask, code: ActivationCode, message: str, upstream_order_no: str = "") -> None:
    now = utcnow()
    code.status = CodeStatus.USED
    code.used_at = now
    task.state = RechargeState.SUCCESS
    task.completed_at = now
    task.updated_at = now
    task.error_code = None
    task.error_message = message or "充值成功"
    if upstream_order_no:
        task.upstream_order_no = upstream_order_no
    task.encrypted_access_token = None
    task.encrypted_session_raw = None
    db.add(code)
    db.add(task)
    db.add(
        AuditLog(
            actor="system",
            action="redeem.success",
            entity_type="recharge_task",
            entity_id=task.task_id,
            detail=f"sku={code.sku_type.value}",
            ip=task.client_ip,
        )
    )
    db.commit()


def _finalize_failure(
    db: Session,
    task: RechargeTask,
    code: ActivationCode | None,
    error_code: str,
    message: str,
    release_code: bool,
    vcc_card_id: int | None = None,
) -> None:
    now = utcnow()
    task.state = RechargeState.FAILED
    task.completed_at = now
    task.updated_at = now
    task.error_code = error_code
    task.error_message = message
    if vcc_card_id:
        task.vcc_card_id = vcc_card_id
    if code and release_code and code.status != CodeStatus.USED:
        code.status = CodeStatus.UNUSED
        code.used_at = None
        db.add(code)
    db.add(task)
    db.add(
        AuditLog(
            actor="system",
            action="redeem.failed",
            entity_type="recharge_task",
            entity_id=task.task_id,
            detail=f"error={error_code} release={release_code}",
            ip=task.client_ip,
        )
    )
    db.commit()


def _reconstruct_session(task: RechargeTask) -> SessionInfo:
    if task.encrypted_session_raw:
        return parse_session(decrypt_text(task.encrypted_session_raw))
    token = decrypt_text(task.encrypted_access_token or "")
    if task.session_kind == "claude":
        return SessionInfo(
            kind="claude",
            email=task.target_email,
            user_id=task.target_user_id,
            access_token=token,
            raw=token,
            expires_at=utcnow() + timedelta(hours=1),
        )
    reconstructed = json.dumps(
        {
            "accessToken": token,
            "user": {"id": task.target_user_id, "email": task.target_email},
            "expires": (utcnow() + timedelta(hours=1)).isoformat() + "Z",
        }
    )
    return parse_session(reconstructed)


def run_recharge_task(task_id: str) -> None:
    """Execute a queued task. Safe to invoke from Celery or a local thread."""
    with redis_lock(f"lock:task:{task_id}") as acquired:
        if not acquired:
            return
        db = SessionLocal()
        try:
            task = db.get(RechargeTask, task_id)
            if not task or task.state in TERMINAL_STATES:
                return
            code = db.get(ActivationCode, task.code_id)
            if not code:
                _finalize_failure(db, task, None, "CODE_NOT_FOUND", "激活码不存在", False)
                return
            task.attempts += 1
            _set_state(db, task, RechargeState.CHECKING_ACCOUNT)

            try:
                session = _reconstruct_session(task)
            except SessionError as exc:
                _finalize_failure(db, task, code, exc.code, exc.message, True)
                return
            except Exception:
                _finalize_failure(
                    db,
                    task,
                    code,
                    "INVALID_SESSION_TOKEN",
                    "Session 凭证已失效，请重新提交",
                    True,
                )
                return

            check = check_account(session)
            if not check.eligible:
                _finalize_failure(
                    db,
                    task,
                    code,
                    check.error_code or "ACCOUNT_NOT_ELIGIBLE",
                    check.message,
                    True,
                )
                return

            if code.channel_mode.value == "SELF_VCC":
                _set_state(db, task, RechargeState.CREATING_CHECKOUT)
                _set_state(db, task, RechargeState.PAYING)

                def on_state(value: str) -> None:
                    if value == "AWAITING_3DS":
                        _set_state(db, task, RechargeState.AWAITING_3DS)

                try:
                    result = run_self_vcc_recharge(db, session, code.sku_type, on_state=on_state)
                except SelfVccFailure as exc:
                    _finalize_failure(
                        db,
                        task,
                        code,
                        exc.code,
                        exc.message,
                        exc.release_code,
                        vcc_card_id=exc.card_id,
                    )
                    return
                except RechargeError as exc:
                    _finalize_failure(db, task, code, exc.code, exc.message, exc.release_code)
                    return
                _finalize_success(db, task, code, result.message)
                return

            _set_state(db, task, RechargeState.PAYING)
            upstream_code = decrypt_text(code.upstream_code_enc) if code.upstream_code_enc else None
            try:
                result = UpstreamProvider().create_recharge(session.raw, code.sku_type.value, upstream_code)
            except RechargeError as exc:
                _finalize_failure(db, task, code, exc.code, exc.message, exc.release_code)
                return
            if result.status != "SUCCESS":
                _finalize_failure(
                    db,
                    task,
                    code,
                    "UPSTREAM_PENDING",
                    result.message or "上游通道仍在处理中，请稍后查看",
                    False,
                )
                return
            _finalize_success(db, task, code, result.message, result.order_no)
        except Exception:  # noqa: BLE001
            logger.exception("Recharge task %s crashed", task_id)
            db.rollback()
            try:
                task = db.get(RechargeTask, task_id)
                code = db.get(ActivationCode, task.code_id) if task else None
                if task and task.state not in TERMINAL_STATES:
                    # A crash during a payment step must never release the code
                    # automatically; leave it locked for manual review.
                    risky = task.state in {RechargeState.PAYING, RechargeState.AWAITING_3DS}
                    _finalize_failure(
                        db,
                        task,
                        code,
                        "INTERNAL_ERROR",
                        "充能引擎内部异常，已转人工处理，请稍后联系客服",
                        release_code=not risky,
                    )
            except Exception:
                db.rollback()
        finally:
            db.close()


def serialize_task(db: Session, task: RechargeTask) -> dict:
    code = db.get(ActivationCode, task.code_id)
    idx = STATE_STEP_INDEX.get(task.state, 0)
    steps = []
    for index, (key, label) in enumerate(STEP_DEFINITIONS):
        if task.state == RechargeState.SUCCESS:
            step_status = "done"
        elif task.state == RechargeState.FAILED and index == idx:
            step_status = "failed"
        elif index < idx:
            step_status = "done"
        elif index == idx:
            step_status = "active"
        else:
            step_status = "waiting"
        steps.append({"key": key, "label": label, "status": step_status})

    message = STATE_MESSAGES.get(task.state, "")
    if task.state == RechargeState.FAILED and task.error_message:
        message = task.error_message
    return {
        "task_id": task.task_id,
        "state": task.state.value,
        "progress_percent": STATE_PROGRESS.get(task.state, 0),
        "step_message": message,
        "steps": steps,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "target_email": mask_email(task.target_email),
        "session_kind": task.session_kind,
        "sku_label": _sku_label(db, code) if code else "",
        "code_status": code.status.value if code else "",
        "can_retry": task.state == RechargeState.FAILED and code is not None and code.status == CodeStatus.UNUSED,
        "created_at": task.created_at.isoformat() + "Z",
        "updated_at": task.updated_at.isoformat() + "Z",
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
    }


def get_task_status(db: Session, task_id: str) -> dict:
    task = db.get(RechargeTask, task_id)
    if not task:
        raise RedeemError("TASK_NOT_FOUND", "充值任务不存在", 404)
    return serialize_task(db, task)


def retry_task(db: Session, task_id: str, ip: str = "") -> RechargeTask:
    task = db.get(RechargeTask, task_id)
    if not task:
        raise RedeemError("TASK_NOT_FOUND", "充值任务不存在", 404)
    if task.state != RechargeState.FAILED:
        raise RedeemError("TASK_NOT_RETRYABLE", "仅失败任务可以重试", 409)
    if not task.encrypted_session_raw and not task.encrypted_access_token:
        raise RedeemError("SESSION_CLEARED", "该任务的 Session 凭证已按安全策略清除，请让用户重新提交", 409)
    code = db.get(ActivationCode, task.code_id)
    if not code:
        raise RedeemError("CODE_NOT_FOUND", "激活码不存在", 404)
    if code.status == CodeStatus.USED:
        raise RedeemError("CODE_ALREADY_USED", "该激活码已完成核销", 409)
    if code.status == CodeStatus.UNUSED:
        code.status = CodeStatus.LOCKED
        code.bound_account_email = code.bound_account_email or task.target_email
        db.add(code)
    task.state = RechargeState.PENDING
    task.error_code = None
    task.error_message = None
    task.completed_at = None
    task.updated_at = utcnow()
    task.attempts += 1
    db.add(task)
    db.add(
        AuditLog(
            actor="admin",
            action="redeem.retry",
            entity_type="recharge_task",
            entity_id=task.task_id,
            detail="",
            ip=ip,
        )
    )
    db.commit()
    dispatch_recharge(task.task_id)
    return task


def cleanup_sensitive_tokens(db: Session) -> int:
    cutoff = utcnow() - timedelta(minutes=settings.token_retention_minutes)
    tasks = (
        db.query(RechargeTask)
        .filter(
            RechargeTask.state.in_(list(TERMINAL_STATES)),
            RechargeTask.updated_at < cutoff,
            RechargeTask.encrypted_access_token.isnot(None),
        )
        .all()
    )
    count = 0
    for task in tasks:
        task.encrypted_access_token = None
        task.encrypted_session_raw = None
        db.add(task)
        count += 1
    if count:
        db.commit()
    return count
