"""SMS receipt state machine.

The zero-loss contract, in one place:

* leasing sets the code to ``LOCKED_WAITING_OTP`` — it is held, not burned;
* only an OTP arriving moves the code to ``USED``;
* every other ending (timeout, customer-requested change, upstream failure)
  cancels upstream for a refund and puts the code back to ``UNUSED``.

So a customer can change numbers freely and a dead region costs nobody money.
The number itself is never burned before an OTP exists.

Expiry is evaluated on read as well as by the sweep, because the sweep interval
is coarser than the 120-second window and a customer refreshing at second 121
must already see the released state.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ActivationCode,
    AuditLog,
    CodeStatus,
    SmsTaskStatus,
    SmsVerificationTask,
    utcnow,
)
from app.services import runtime_settings as runtime
from app.services.crypto import mask_phone
from app.services.locks import redis_lock
from app.services.sms import providers

logger = logging.getLogger(__name__)

# Services a customer can pick, mapped to each supplier's own vocabulary.
# Anything not listed falls back to the raw key, which both suppliers accept
# for their long tail.
SERVICE_ALIASES = {
    "smspool": {
        "claude": "claudeai",
        "chatgpt": "openai",
        "google": "google",
        "apple": "apple",
        "telegram": "telegram",
        "cursor": "openai",
    },
    "fivesim": {
        "claude": "claude",
        "chatgpt": "openai",
        "google": "google",
        "apple": "apple",
        "telegram": "telegram",
        "cursor": "openai",
    },
}

SERVICE_LABELS = {
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "google": "Google",
    "apple": "Apple ID",
    "telegram": "Telegram",
    "cursor": "Cursor",
}

# Recommended first: a US Non-VoIP range clears most of these services.
COUNTRY_LABELS = {
    "US": "美国 · Non-VoIP 高通过率",
    "GB": "英国 · 实体卡号段",
    "PH": "菲律宾 · 低成本",
}


class SmsError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def window_seconds() -> int:
    return runtime.get_int("sms_receive_window_seconds", settings.sms_receive_window_seconds)


def _service_for(provider: str, service: str) -> str:
    key = (service or "").strip().lower()
    return SERVICE_ALIASES.get(provider, {}).get(key, key)


def _release_code(db: Session, task: SmsVerificationTask) -> ActivationCode | None:
    """Undo the hold. Never releases a code that was already consumed."""
    code = db.get(ActivationCode, task.code_id)
    if code is not None and code.status in {CodeStatus.LOCKED_WAITING_OTP, CodeStatus.LOCKED}:
        code.status = CodeStatus.UNUSED
        code.used_at = None
        db.add(code)
    return code


def _cancel_upstream(task: SmsVerificationTask) -> None:
    """Return the number for a refund. Best-effort by design."""
    if not task.upstream_order_id:
        return
    try:
        provider = providers.build_provider(task.provider)
        lease = providers.LeasedNumber(
            upstream_order_id=task.upstream_order_id,
            phone_number=task.phone_number,
            provider=task.provider,
        )
        provider.cancel(lease)
    except Exception:  # noqa: BLE001 - a refund hiccup must not block the customer
        logger.warning("SMS cancel failed for task %s", task.task_id, exc_info=False)


def _finish(db: Session, task: SmsVerificationTask, status: SmsTaskStatus, error: str = "") -> None:
    task.status = status
    task.error_message = error[:250] or None
    task.completed_at = utcnow()
    task.updated_at = utcnow()
    db.add(task)


def _active_task(db: Session, code_id: int) -> SmsVerificationTask | None:
    return (
        db.query(SmsVerificationTask)
        .filter(
            SmsVerificationTask.code_id == code_id,
            SmsVerificationTask.status == SmsTaskStatus.WAITING,
        )
        .order_by(SmsVerificationTask.created_at.desc())
        .first()
    )


def _expire_if_due(db: Session, task: SmsVerificationTask) -> None:
    if task.status != SmsTaskStatus.WAITING or task.expires_at > utcnow():
        return
    _cancel_upstream(task)
    _finish(db, task, SmsTaskStatus.EXPIRED, "120 秒内未收到验证码")
    _release_code(db, task)


def lease(db: Session, code: ActivationCode, service: str, country: str, ip: str = "") -> SmsVerificationTask:
    """Lease a number, holding the code rather than consuming it."""
    if not code.sku_type.is_sms:
        raise SmsError("NOT_SMS_CODE", "该激活码不是接码类商品")
    if code.status == CodeStatus.USED:
        raise SmsError("CODE_ALREADY_USED", "该激活码已完成接码")
    if code.status == CodeStatus.DISABLED:
        raise SmsError("CODE_DISABLED", "该激活码已作废")

    with redis_lock(f"lock:sms:{code.code}") as acquired:
        if not acquired:
            raise SmsError("SMS_BUSY", "正在取号，请稍候")

        db.expire_all()
        code = db.query(ActivationCode).filter(ActivationCode.id == code.id).with_for_update().first()
        if code is None:
            raise SmsError("CODE_NOT_FOUND", "激活码不存在", 404)
        if code.status == CodeStatus.USED:
            raise SmsError("CODE_ALREADY_USED", "该激活码已完成接码")

        existing = _active_task(db, code.id)
        if existing:
            _expire_if_due(db, existing)
            if existing.status == SmsTaskStatus.WAITING:
                db.commit()
                return existing

        provider = providers.choose_provider(runtime.get("sms_default_provider") or None)
        upstream_service = _service_for(provider.name, service)
        try:
            lease_info = provider.lease_number(upstream_service, (country or "US").upper())
        except providers.NoNumbersAvailable:
            # Nothing was held at the supplier, so nothing is refunded and the
            # code must stay usable for another region.
            if code.status == CodeStatus.LOCKED_WAITING_OTP:
                code.status = CodeStatus.UNUSED
                db.add(code)
            db.commit()
            raise SmsError("NO_NUMBERS", "该地区暂时没有可用号码，请换一个地区或稍后再试")
        except providers.SmsProviderError as exc:
            if code.status == CodeStatus.LOCKED_WAITING_OTP:
                code.status = CodeStatus.UNUSED
                db.add(code)
            db.commit()
            raise SmsError(exc.code, exc.message, 502) from exc

        now = utcnow()
        task = SmsVerificationTask(
            task_id=str(uuid.uuid4()),
            code_id=code.id,
            provider=provider.name,
            upstream_order_id=lease_info.upstream_order_id,
            service_name=service,
            country_code=(country or "US").upper(),
            phone_number=lease_info.phone_number,
            status=SmsTaskStatus.WAITING,
            client_ip=ip,
            expires_at=now + timedelta(seconds=window_seconds()),
            created_at=now,
            updated_at=now,
        )
        code.status = CodeStatus.LOCKED_WAITING_OTP
        db.add(task)
        db.add(
            AuditLog(
                actor="customer",
                action="sms.lease",
                entity_type="sms_task",
                entity_id=task.task_id,
                detail=f"provider={provider.name} service={service} country={task.country_code}",
                ip=ip,
            )
        )
        db.commit()
        db.refresh(task)
        return task


def poll(db: Session, task_id: str) -> SmsVerificationTask:
    """Check upstream once; consume the code only when an OTP is parsed."""
    task = db.get(SmsVerificationTask, task_id)
    if task is None:
        raise SmsError("SMS_TASK_NOT_FOUND", "接码任务不存在", 404)
    if task.status != SmsTaskStatus.WAITING:
        return task

    _expire_if_due(db, task)
    if task.status != SmsTaskStatus.WAITING:
        db.commit()
        return task

    try:
        provider = providers.build_provider(task.provider)
        result = provider.poll_code(
            providers.LeasedNumber(
                upstream_order_id=task.upstream_order_id or "",
                phone_number=task.phone_number,
                provider=task.provider,
            )
        )
    except providers.SmsProviderError as exc:
        if not exc.transient:
            task.error_message = exc.message[:250]
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    if result.arrived:
        code = db.get(ActivationCode, task.code_id)
        task.raw_sms = (result.text or "")[:1000]
        if result.code and code is not None:
            code.status = CodeStatus.USED
            code.used_at = utcnow()
            db.add(code)
            task.otp_code = result.code
            _finish(db, task, SmsTaskStatus.RECEIVED)
        else:
            # A message we could not parse still means the number worked; hold
            # the code and let the customer read the raw text.
            task.error_message = "已收到短信，但未能自动识别验证码，请查看原文"
            task.status = SmsTaskStatus.RECEIVED if not result.text else task.status
            db.add(task)
        db.commit()
        db.refresh(task)
        return task

    task.updated_at = utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def change_number(db: Session, task_id: str, ip: str = "") -> SmsVerificationTask:
    """Cancel (refund) the current number and lease a fresh one on the same code.

    Used both by the customer's "no code? change number" button and by the
    timeout sweep, which makes the two paths identical.
    """
    task = db.get(SmsVerificationTask, task_id)
    if task is None:
        raise SmsError("SMS_TASK_NOT_FOUND", "接码任务不存在", 404)

    if task.status == SmsTaskStatus.RECEIVED:
        return task
    if task.status in {SmsTaskStatus.CANCELLED, SmsTaskStatus.EXPIRED, SmsTaskStatus.FAILED}:
        # Nothing live to cancel; hand back the same terminal row.
        return task

    _cancel_upstream(task)
    code = _release_code(db, task)
    _finish(db, task, SmsTaskStatus.CANCELLED, "用户申请换号")
    db.add(
        AuditLog(
            actor="customer",
            action="sms.change_number",
            entity_type="sms_task",
            entity_id=task.task_id,
            detail=f"service={task.service_name} country={task.country_code}",
            ip=ip,
        )
    )
    db.commit()

    if code is None:
        raise SmsError("CODE_NOT_FOUND", "激活码不存在", 404)
    return lease(db, code, task.service_name, task.country_code, ip)


def sweep_expired(db: Session) -> int:
    """Expire overdue tasks and refund them. Driven by Celery Beat."""
    due = (
        db.query(SmsVerificationTask)
        .filter(
            SmsVerificationTask.status == SmsTaskStatus.WAITING,
            SmsVerificationTask.expires_at <= utcnow(),
        )
        .limit(200)
        .all()
    )
    for task in due:
        _expire_if_due(db, task)
    if due:
        db.commit()
    return len(due)


def serialize(task: SmsVerificationTask, code: ActivationCode | None = None) -> dict:
    remaining = max(0, int((task.expires_at - utcnow()).total_seconds()))
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "phone_number": task.phone_number,
        "phone_masked": mask_phone(task.phone_number),
        "service": task.service_name,
        "service_label": SERVICE_LABELS.get(task.service_name, task.service_name),
        "country": task.country_code,
        "country_label": COUNTRY_LABELS.get(task.country_code, task.country_code),
        "otp_code": task.otp_code,
        "raw_sms": task.raw_sms,
        "seconds_remaining": remaining,
        "window_seconds": window_seconds(),
        "code_status": code.status.value if code else "",
        "error_message": task.error_message,
        # The button appears at 45 seconds; the server states it so the rule
        # lives in one place.
        "can_change_number": task.status == SmsTaskStatus.WAITING and remaining <= window_seconds() - 45,
        "expires_at": task.expires_at.isoformat() + "Z",
    }


def options() -> dict:
    return {
        "services": [{"key": key, "label": label} for key, label in SERVICE_LABELS.items()],
        "countries": [{"key": key, "label": label} for key, label in COUNTRY_LABELS.items()],
        "provider_ready": bool(providers.available_providers()),
        "window_seconds": window_seconds(),
    }


__all__ = [
    "COUNTRY_LABELS",
    "SERVICE_LABELS",
    "SmsError",
    "change_number",
    "lease",
    "options",
    "poll",
    "serialize",
    "sweep_expired",
    "window_seconds",
]
