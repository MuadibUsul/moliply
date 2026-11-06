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
from app.services.fulfillment import self_vcc_circuit, stripe_playwright, vcc_pool_scheduler
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
        error_detail: str = "",
        artifact_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.can_fallback = can_fallback
        self.notify = notify
        self.error_detail = error_detail
        self.artifact_path = artifact_path


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


def upstream_configured() -> bool:
    """True only when an upstream API base and merchant key are both present.

    ``mock`` counts as configured because the mock channel always answers.
    """
    mode = runtime.get("upstream_mode", settings.upstream_mode).lower()
    if mode == "mock":
        return True
    return bool(
        runtime.get("upstream_api_base", settings.upstream_api_base)
        and runtime.get("upstream_api_key", settings.upstream_api_key)
    )


def _attempt_upstream(db: Session, task: RechargeTask, code: ActivationCode, session: SessionInfo) -> DispatchOutcome:
    task.executed_channel = UPSTREAM
    task.executed_region = None
    task.billed_currency = None
    task.billed_amount = None
    db.add(task)
    db.commit()
    _set_state(db, task, RechargeState.PAYING)
    upstream_code = decrypt_text(code.upstream_code_enc) if code.upstream_code_enc else None
    # Redemption channels want the sessionToken (or a JSON carrying it); the
    # accessToken alone is not accepted. Claude is the opposite: the raw
    # sessionKey is exactly right, and its raw can be a whole cookie string.
    credential = (
        (session.session_token or session.raw) if session.kind == "chatgpt" else session.access_token
    )
    result = UpstreamProvider().create_recharge(credential, code.sku_type.value, upstream_code)
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
            "自营通道处于演练模式，本次跳过自营",
            can_fallback=True,
            notify=False,
        )
    if self_vcc_circuit.is_open():
        # The breaker already alerted when it opened; alerting on every skipped
        # order afterwards would only train the operator to ignore it.
        raise ChannelFallback(
            "SELF_VCC_CIRCUIT_OPEN",
            "自营通道连续故障，已自动熔断，本次直接走上游",
            can_fallback=True,
            notify=False,
        )

    last_error: ChannelFallback | None = None
    for region in _candidate_regions(code.preferred_region, session):
        card = None
        reserved = False
        try:
            proxy = region_router.proxy_for(region)
            currency, amount = region_router.amount_for(region, code.sku_type)
            card = vcc_pool_scheduler.acquire_card(db, region, amount=region_router.cost_usd_for(region, code.sku_type))
            if card is None:
                raise ChannelFallback("NO_ACTIVE_VCC_IN_POOL", f"{region} 卡池暂无可用卡片")
            reserved = True
            billing = region_router.billing_for(card, region)
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
            vcc_pool_scheduler.mark_success(db, card, region, charged=region_router.cost_usd_for(region, code.sku_type))
            reserved = False
            self_vcc_circuit.reset()
            return DispatchOutcome(
                channel=SELF_VCC,
                region=region,
                currency=currency,
                amount=amount,
                message=message,
            )
        except ChannelFallback as exc:
            if reserved and card is not None:
                vcc_pool_scheduler.release_reservation(db, card)
                reserved = False
            last_error = exc
            if exc.can_fallback:
                # Region-specific failure: let the outer loop try the other region.
                continue
            raise
        except SelfVccFailure as exc:
            detail = _failure_detail(exc)
            # ``CARD_DECLINED`` is itself decisive, so old raise sites that only
            # set the code keep working.
            declined = exc.is_explicit_decline or exc.code == "CARD_DECLINED"
            if reserved and card is not None:
                if declined:
                    vcc_pool_scheduler.record_failure(db, card, exc.message)
                else:
                    vcc_pool_scheduler.release_reservation(db, card)
                reserved = False

            if declined:
                # Stripe refused the card, so nothing was charged; the card is
                # parked but the customer can still be served upstream.
                last_error = ChannelFallback(
                    exc.code, detail, error_detail=exc.error_detail, artifact_path=exc.artifact_path
                )
                continue
            if not exc.payment_submitted:
                # Nothing was charged: a broken page or a dead proxy, so count
                # it toward the breaker and let the other region try.
                self_vcc_circuit.record_failure(exc.code)
                last_error = ChannelFallback(
                    exc.code, detail, error_detail=exc.error_detail, artifact_path=exc.artifact_path
                )
                continue

            # Submitted with an unknown outcome: a second region, and a later
            # upstream retry, could both charge. Lock for a human instead.
            raise ChannelFallback(
                "PAYMENT_OUTCOME_UNKNOWN",
                detail,
                can_fallback=False,
                error_detail=exc.error_detail,
                artifact_path=exc.artifact_path,
            ) from exc
        except region_router.RegionError as exc:
            if reserved and card is not None:
                vcc_pool_scheduler.release_reservation(db, card)
                reserved = False
            self_vcc_circuit.record_failure(exc.code)
            last_error = ChannelFallback(exc.code, exc.message)
            continue
        except RechargeError:
            if reserved and card is not None:
                vcc_pool_scheduler.release_reservation(db, card)
            raise
        except Exception as exc:  # noqa: BLE001
            if reserved and card is not None:
                vcc_pool_scheduler.release_reservation(db, card)
            logger.warning("Self-VCC unexpected failure: %s", exc.__class__.__name__)
            self_vcc_circuit.record_failure(exc.__class__.__name__)
            raise ChannelFallback("SELF_VCC_ERROR", "自营通道执行异常，已转人工处理", can_fallback=False) from exc

    if last_error:
        raise last_error
    raise ChannelFallback("NO_ACTIVE_VCC_IN_POOL", "自营卡池暂无可用卡片")


def _failure_detail(exc: SelfVccFailure) -> str:
    """Message plus whatever the page showed, for the task record."""
    detail = exc.message or exc.code
    if exc.error_detail and exc.error_detail not in detail:
        detail = f"{detail}（页面提示：{exc.error_detail}）"
    return detail[:500]


def _as_recharge_error(exc: ChannelFallback, *, release_code: bool | None = None) -> RechargeError:
    """Convert a routing decision into the customer-safe error the task stores."""
    error = RechargeError(
        exc.code,
        exc.message,
        release_code=exc.can_fallback if release_code is None else release_code,
    )
    error.error_detail = exc.error_detail
    error.artifact_path = exc.artifact_path
    return error


def dispatch(db: Session, task: RechargeTask, code: ActivationCode, session: SessionInfo) -> DispatchOutcome:
    """Route a task: the local pool is the default, upstream is the optional net.

    An upstream that is simply not configured is no longer an error. The task
    runs on the local card pool, and only an exhaustion/failure with a configured
    upstream falls back; without one the pool error surfaces for manual review.
    """
    mode = effective_mode(code)
    if mode == UPSTREAM_ONLY:
        return _attempt_upstream(db, task, code, session)

    if mode == SELF_VCC_ONLY:
        try:
            return _attempt_self(db, task, code, session)
        except ChannelFallback as exc:
            raise _as_recharge_error(exc) from exc

    # HYBRID_SELF_FIRST
    if not runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled):
        # Self channel switched off at runtime: prefer the pool only if it exists,
        # otherwise the disabled state already means "go upstream".
        if not upstream_configured():
            raise RechargeError(
                "SELF_VCC_DISABLED",
                "自营卡池未启用，且未配置上游开卡接口，请联系客服",
                release_code=True,
            )
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
            raise _as_recharge_error(exc, release_code=False) from exc
        if not upstream_configured():
            # No upstream to land on: report the pool condition directly instead
            # of surfacing a confusing "upstream unconfigured" error.
            raise _as_recharge_error(exc) from exc
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
        if not upstream_configured():
            raise RechargeError(exc.code, exc.message, release_code=True) from exc
        return _attempt_upstream(db, task, code, session)
