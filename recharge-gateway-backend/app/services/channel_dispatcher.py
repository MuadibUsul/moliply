"""V2 hybrid routing: self PH -> self US -> V1 upstream fallback.

V1 ``UPSTREAM`` codes keep their original behavior. New codes can use
``HYBRID_SELF_FIRST``, ``SELF_VCC_ONLY`` or ``UPSTREAM_ONLY``. The global
``routing_policy`` setting is a maintenance/kill switch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ActivationCode,
    ChannelMode,
    RechargeState,
    RechargeTask,
    RegionPreference,
    utcnow,
)
from app.services import feishu
from app.services import region_router
from app.services import runtime_settings as runtime
from app.services import stripe_playwright
from app.services import vcc_pool_scheduler
from app.services.crypto import decrypt_text
from app.services.errors import RechargeError
from app.services.self_vcc_worker import SelfVccFailure
from app.services.session_parser import SessionInfo
from app.services.upstream_provider import UpstreamProvider, UpstreamResult

logger = logging.getLogger(__name__)

UPSTREAM = "UPSTREAM"
SELF_VCC = "SELF_VCC"
HYBRID_SELF_FIRST = "HYBRID_SELF_FIRST"
SELF_VCC_ONLY = "SELF_VCC_ONLY"
UPSTREAM_ONLY = "UPSTREAM_ONLY"


@dataclass
class DispatchOutcome:
    channel: str
    message: str
    region: str | None = None
    currency: str | None = None
    amount: Decimal | None = None
    upstream_order_no: str = ""


class ChannelFallback(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        can_fallback: bool = True,
        notify: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.can_fallback = can_fallback
        self.notify = notify


def effective_mode(code: ActivationCode) -> str:
    policy = runtime.get("routing_policy", settings.routing_policy).upper()
    if policy == UPSTREAM_ONLY:
        return UPSTREAM_ONLY
    if policy == SELF_VCC_ONLY:
        return SELF_VCC_ONLY
    mode = code.channel_mode
    if mode in {ChannelMode.UPSTREAM, ChannelMode.UPSTREAM_ONLY}:
        return UPSTREAM_ONLY
    if mode in {ChannelMode.SELF_VCC, ChannelMode.SELF_VCC_ONLY}:
        return SELF_VCC_ONLY
    return HYBRID_SELF_FIRST


def _set_state(db: Session, task: RechargeTask, state: RechargeState) -> None:
    task.state = state
    task.updated_at = utcnow()
    db.add(task)
    db.commit()


def _attempt_upstream(db: Session, task: RechargeTask, code: ActivationCode, session: SessionInfo) -> DispatchOutcome:
    task.executed_channel = UPSTREAM
    task.executed_region = None
    task.billed_currency = None
    task.billed_amount = None
    db.add(task)
    db.commit()
    _set_state(db, task, RechargeState.PAYING)
    upstream_code = decrypt_text(code.upstream_code_enc) if code.upstream_code_enc else None
    result = UpstreamProvider().create_recharge(session.raw, code.sku_type.value, upstream_code)
    if result.status != "SUCCESS":
        raise RechargeError(
            "UPSTREAM_PENDING",
            result.message or "上游通道仍在处理中，请稍后查看",
            release_code=False,
        )
    task.upstream_order_no = result.order_no
    db.add(task)
    db.commit()
    return DispatchOutcome(
        channel=UPSTREAM,
        message=result.message or "上游通道充值成功",
        upstream_order_no=result.order_no,
    )


def _candidate_regions(preferred: RegionPreference, session: SessionInfo) -> list[str]:
    selected = region_router.determine_region(preferred, session)
    if selected == region_router.US:
        return [region_router.US]
    # PH first, then US if the account/card cannot use PHP.
    return [region_router.PH, region_router.US]


def _attempt_self(db: Session, task: RechargeTask, code: ActivationCode, session: SessionInfo) -> DispatchOutcome:
    if not runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled):
        raise ChannelFallback("SELF_VCC_DISABLED", "自营通道未启用", notify=False)
    if runtime.get_bool("self_vcc_dry_run", settings.self_vcc_dry_run):
        raise ChannelFallback(
            "SELF_VCC_DRY_RUN",
            "自营通道处于演练模式，已停止在提交支付之前",
            can_fallback=False,
            notify=False,
        )

    last_error: ChannelFallback | None = None
    for region in _candidate_regions(code.preferred_region, session):
        card = None
        try:
            proxy = region_router.proxy_for(region)
            card = vcc_pool_scheduler.acquire_card(db, region)
            if card is None:
                raise ChannelFallback("NO_ACTIVE_VCC_IN_POOL", f"{region} 卡池暂无可用卡片")
            billing = region_router.billing_for(card, region)
            currency, amount = region_router.amount_for(region, code.sku_type)
            card_id = card.id
            task.vcc_card_id = card_id
            task.executed_channel = SELF_VCC
            task.executed_region = region
            task.billed_currency = currency
            task.billed_amount = amount
            db.add(task)
            db.commit()

            _set_state(db, task, RechargeState.CREATING_CHECKOUT)
            checkout_url = region_router.create_checkout_url(session, region, code.sku_type)
            _set_state(db, task, RechargeState.PAYING)
            card_data = {
                "number": decrypt_text(card.card_number_enc),
                "cvv": decrypt_text(card.cvv_enc),
                "exp_month": card.exp_month,
                "exp_year": card.exp_year,
                "holder_name": card.holder_name,
                "billing": billing,
                "three_ds_email": card.three_ds_email_account,
                "timezone": "Asia/Manila" if region == region_router.PH else "America/New_York",
            }

            def on_state(value: str) -> None:
                if value == "AWAITING_3DS":
                    _set_state(db, task, RechargeState.AWAITING_3DS)

            message = stripe_playwright.checkout_and_cancel_autorenew(
                checkout_url,
                card_data,
                proxy,
                on_state=on_state,
            )
            vcc_pool_scheduler.mark_success(db, card, region)
            return DispatchOutcome(
                channel=SELF_VCC,
                region=region,
                currency=currency,
                amount=amount,
                message=message,
            )
        except ChannelFallback as exc:
            last_error = exc
            if exc.can_fallback and exc.code != "NO_ACTIVE_VCC_IN_POOL":
                # Region-specific failure: let the outer loop try the other region.
                continue
            if not exc.can_fallback:
                raise
            # No card in PH: still try US.
            continue
        except SelfVccFailure as exc:
            # Only a declined card is a hard card-level signal; suspend it.
            if exc.code == "CARD_DECLINED" and 'card' in locals() and card is not None:
                vcc_pool_scheduler.suspend(db, card, exc.message)
            fallbackable = exc.release_code
            if fallbackable:
                last_error = ChannelFallback(exc.code, exc.message)
                continue
            raise ChannelFallback(exc.code, exc.message, can_fallback=False) from exc
        except region_router.RegionError as exc:
            last_error = ChannelFallback(exc.code, exc.message)
            continue
        except RechargeError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Self-VCC unexpected failure: %s", exc.__class__.__name__)
            raise ChannelFallback("SELF_VCC_ERROR", "自营通道执行异常，已转人工处理", can_fallback=False) from exc

    if last_error:
        raise last_error
    raise ChannelFallback("NO_ACTIVE_VCC_IN_POOL", "自营卡池暂无可用卡片")


def dispatch(db: Session, task: RechargeTask, code: ActivationCode, session: SessionInfo) -> DispatchOutcome:
    mode = effective_mode(code)
    if mode == UPSTREAM_ONLY:
        return _attempt_upstream(db, task, code, session)

    if mode == SELF_VCC_ONLY:
        try:
            return _attempt_self(db, task, code, session)
        except ChannelFallback as exc:
            raise RechargeError(
                exc.code,
                exc.message,
                release_code=exc.can_fallback,
            ) from exc

    # HYBRID_SELF_FIRST
    if not runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled):
        task.fallback_reason = "SELF_VCC_DISABLED"
        db.add(task)
        db.commit()
        return _attempt_upstream(db, task, code, session)

    try:
        return _attempt_self(db, task, code, session)
    except ChannelFallback as exc:
        task.fallback_reason = f"{exc.code}: {exc.message}"[:250]
        db.add(task)
        db.commit()
        if not exc.can_fallback:
            raise RechargeError(exc.code, exc.message, release_code=False) from exc
        if exc.notify:
            feishu.dispatch_event(
                "channel.fallback",
                "MOLIPLY 自营通道降级",
                {
                    "任务编号": task.task_id,
                    "商品": code.sku_type.value,
                    "原因": exc.message[:160],
                },
                severity="warning",
                link_path="/admin/tasks",
            )
        return _attempt_upstream(db, task, code, session)
    except region_router.RegionError as exc:
        task.fallback_reason = f"{exc.code}: {exc.message}"[:250]
        db.add(task)
        db.commit()
        return _attempt_upstream(db, task, code, session)
