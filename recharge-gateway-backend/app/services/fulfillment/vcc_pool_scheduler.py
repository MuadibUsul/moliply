"""V2 VCC pool scheduler with amount-aware reservation and failure fuses.

A card is only handed to a worker after the order amount has been reserved
against it, so a second worker started while the first checkout is still in
flight cannot spend the same dollars on the same card. Reservations are released
or committed when the checkout ends.

Failure handling escalates in three steps: a single decline parks the card for a
cooldown window, repeated consecutive declines suspend it, and a hard decline
(card cancelled/expired/fraudulent) suspends it immediately. Suspension and pool
depletion both raise an operator alert.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RechargeTask, VccCard, VccStatus
from app.services import region_router
from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)

HARD_DECLINE_KEYWORDS = (
    "declined",
    "stolen_card",
    "expired_card",
    "fraudulent",
    "transaction_not_allowed",
)


class NoAvailableVccError(Exception):
    code = "NO_ACTIVE_VCC_IN_POOL"


def max_consecutive_failures() -> int:
    return runtime.get_int("vcc_max_consecutive_failures", settings.vcc_max_consecutive_failures)


def cooldown_minutes() -> int:
    return runtime.get_int("vcc_cooldown_minutes", settings.vcc_cooldown_minutes)


def pool_watermark() -> int:
    return runtime.get_int("vcc_pool_watermark", settings.vcc_pool_watermark)


def _alert(event: str, title: str, fields: dict, severity: str = "warning") -> None:
    """Fan a pool condition out to Feishu and, if configured, SMTP."""
    from app.services import feishu, mailer

    try:
        feishu.dispatch_event(event, title, fields, severity=severity, link_path="/admin/cards")
    except Exception:  # noqa: BLE001
        logger.debug("Feishu pool alert failed", exc_info=False)
    try:
        mailer.send_operator_email(title, fields)
    except Exception:  # noqa: BLE001
        logger.debug("SMTP pool alert failed", exc_info=False)


def acquire_card(db: Session, region: str, amount: Decimal | None = None) -> VccCard | None:
    """Reserve one bind slot *and* the order amount before a slow checkout starts.

    ``available_balance - reserved_balance`` is the spendable remainder, so one
    card can still serve several small orders but never gets over-committed. The
    row is locked with ``SKIP LOCKED`` so concurrent workers fall through to other
    cards instead of queueing behind the same row.

    The caller must call :func:`release_reservation` when the checkout definitely
    did not charge, or :func:`mark_success` after a successful charge.
    """
    now = datetime.utcnow()
    minimum = region_router.min_balance_for(region)
    required = amount if amount is not None and amount > minimum else minimum
    spendable = VccCard.available_balance - VccCard.reserved_balance

    query = db.query(VccCard).filter(
        VccCard.status == VccStatus.ACTIVE,
        spendable >= required,
        VccCard.current_bind_count < VccCard.max_bind_count,
        VccCard.failed_count == 0,
        (VccCard.cooldown_until.is_(None)) | (VccCard.cooldown_until <= now),
    )
    if region == region_router.PH:
        query = query.filter(VccCard.ph_billing_address_json.isnot(None))
    card = (
        query.order_by(
            VccCard.consecutive_failures.asc(),
            VccCard.available_balance.desc(),
            VccCard.last_used_at.asc().nullsfirst(),
            VccCard.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if card is None:
        check_watermark(db, required)
        return None
    card.current_bind_count += 1
    card.reserved_balance = (card.reserved_balance or Decimal("0")) + required
    card.is_locked = True
    card.locked_at = now
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def peek_card(db: Session, region: str, amount: Decimal | None = None) -> VccCard | None:
    """Read-only look at the next card that would be chosen, without reserving it."""
    now = datetime.utcnow()
    minimum = region_router.min_balance_for(region)
    required = amount if amount is not None and amount > minimum else minimum
    spendable = VccCard.available_balance - VccCard.reserved_balance
    query = db.query(VccCard).filter(
        VccCard.status == VccStatus.ACTIVE,
        spendable >= required,
        VccCard.current_bind_count < VccCard.max_bind_count,
        VccCard.failed_count == 0,
        (VccCard.cooldown_until.is_(None)) | (VccCard.cooldown_until <= now),
    )
    if region == region_router.PH:
        query = query.filter(VccCard.ph_billing_address_json.isnot(None))
    return (
        query.order_by(
            VccCard.consecutive_failures.asc(),
            VccCard.available_balance.desc(),
            VccCard.last_used_at.asc().nullsfirst(),
            VccCard.id.asc(),
        ).first()
    )


def release_reservation(db: Session, card: VccCard, amount: Decimal | None = None) -> None:
    """Return an unused reservation; never call this after a successful charge."""
    reserved = amount if amount is not None else (card.reserved_balance or Decimal("0"))
    card.reserved_balance = max(Decimal("0"), (card.reserved_balance or Decimal("0")) - reserved)
    card.current_bind_count = max(0, card.current_bind_count - 1)
    card.is_locked = False
    card.locked_at = None
    db.add(card)
    db.commit()


def mark_success(db: Session, card: VccCard, region: str, charged: Decimal | None = None) -> None:
    """Settle a charged checkout: burn the money, clear the fuse state."""
    now = datetime.utcnow()
    cost = charged if charged is not None else region_router.cost_usd_for(region)
    # The whole promise is released; the real charge hits the balance. They differ
    # when a reservation was padded to the region's minimum usable balance.
    card.reserved_balance = Decimal("0")
    card.available_balance = max(Decimal("0"), card.available_balance - cost)
    card.total_used_count = (card.total_used_count or 0) + 1
    card.consecutive_failures = 0
    card.cooldown_until = None
    card.last_error = None
    card.last_used_at = now
    card.is_locked = False
    card.locked_at = None
    if card.current_bind_count >= card.max_bind_count:
        card.status = VccStatus.EXHAUSTED
    elif card.available_balance - (card.reserved_balance or Decimal("0")) < region_router.min_balance_for(region):
        card.status = VccStatus.EXHAUSTED
    db.add(card)
    db.commit()
    check_watermark(db, region_router.min_balance_for(region))


def suspend(db: Session, card: VccCard, reason: str = "", hard: bool = False) -> None:
    """Suspend a card immediately (hard decline) or once the fuse threshold trips."""
    card.failed_count += 1
    card.consecutive_failures = (card.consecutive_failures or 0) + 1
    card.last_error = (reason or "declined")[:255]
    card.is_locked = False
    card.locked_at = None
    threshold = max_consecutive_failures()
    if hard or card.consecutive_failures >= threshold:
        card.status = VccStatus.SUSPENDED
        db.add(card)
        db.commit()
        logger.warning("VCC card id=%s suspended: %s", card.id, reason or "declined")
        _alert(
            "vcc.card_suspended",
            "MOLIPLY 自营卡片已挂起",
            {
                "卡片 ID": card.id,
                "卡头 BIN": card.card_bin,
                "连续失败": card.consecutive_failures,
                "原因": (reason or "拒付")[:160],
            },
            severity="warning",
        )
        return
    card.cooldown_until = datetime.utcnow() + timedelta(minutes=cooldown_minutes())
    db.add(card)
    db.commit()
    logger.warning(
        "VCC card id=%s cooling down for %s min (%s/%s)",
        card.id,
        cooldown_minutes(),
        card.consecutive_failures,
        threshold,
    )


def is_hard_decline(reason: str) -> bool:
    lowered = (reason or "").lower()
    return any(keyword in lowered for keyword in HARD_DECLINE_KEYWORDS)


def record_failure(db: Session, card: VccCard, reason: str, amount: Decimal | None = None) -> None:
    """Release the reservation and let the fuse decide between cooldown and suspension."""
    release_reservation(db, card, amount)
    suspend(db, card, reason, hard=is_hard_decline(reason))


def check_watermark(db: Session, required: Decimal) -> int:
    """Count cards still able to serve one more order and alert below the watermark."""
    now = datetime.utcnow()
    spendable = VccCard.available_balance - VccCard.reserved_balance
    healthy = (
        db.query(func.count(VccCard.id))
        .filter(
            VccCard.status == VccStatus.ACTIVE,
            VccCard.failed_count == 0,
            VccCard.is_locked.is_(False),
            spendable >= required,
            (VccCard.cooldown_until.is_(None)) | (VccCard.cooldown_until <= now),
        )
        .scalar()
        or 0
    )
    if healthy < pool_watermark():
        _alert(
            "vcc.pool_low",
            "MOLIPLY 自营卡池低于预警线",
            {
                "可承载订单的卡片": healthy,
                "预警阈值": pool_watermark(),
                "单笔所需余额(USD)": f"{required:.2f}",
            },
            severity="warning",
        )
    return int(healthy)


def mark_closed(db: Session, card: VccCard) -> None:
    card.status = VccStatus.CLOSED
    card.available_balance = Decimal("0")
    card.reserved_balance = Decimal("0")
    card.is_locked = False
    card.locked_at = None
    db.add(card)
    db.commit()


def calibrate_balance(db: Session, card: VccCard, balance: Decimal) -> None:
    """Re-sync a card's balance after an out-of-band top-up or spend."""
    card.available_balance = max(Decimal("0"), balance)
    if card.status == VccStatus.EXHAUSTED and card.available_balance > Decimal("0"):
        card.status = VccStatus.ACTIVE
    db.add(card)
    db.commit()


def summary(db: Session) -> dict:
    cards = db.query(VccCard).all()
    now = datetime.utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
    channel_rows = (
        db.query(RechargeTask.executed_channel, func.count(RechargeTask.task_id))
        .filter(RechargeTask.created_at >= start)
        .group_by(RechargeTask.executed_channel)
        .all()
    )
    channels = {str(key or "PENDING"): int(count) for key, count in channel_rows}
    fallback = (
        db.query(func.count(RechargeTask.task_id))
        .filter(RechargeTask.created_at >= start, RechargeTask.fallback_reason.isnot(None))
        .scalar()
        or 0
    )
    return {
        "total": len(cards),
        "active": sum(1 for card in cards if card.status == VccStatus.ACTIVE),
        "available": sum(
            1
            for card in cards
            if card.status == VccStatus.ACTIVE
            and card.current_bind_count < card.max_bind_count
            and not card.is_locked
        ),
        "locked": sum(1 for card in cards if card.is_locked),
        "cooling": sum(1 for card in cards if card.cooldown_until and card.cooldown_until > now),
        "exhausted": [card.id for card in cards if card.status == VccStatus.EXHAUSTED],
        "suspended": [card.id for card in cards if card.status == VccStatus.SUSPENDED],
        "closed": sum(1 for card in cards if card.status == VccStatus.CLOSED),
        "available_balance_usd": float(sum((card.available_balance for card in cards), Decimal("0"))),
        "reserved_balance_usd": float(sum((card.reserved_balance or Decimal("0") for card in cards), Decimal("0"))),
        "watermark": pool_watermark(),
        "today_self": channels.get("SELF_VCC", 0),
        "today_upstream": channels.get("UPSTREAM", 0),
        "today_fallback": int(fallback),
        "routing_policy": region_router.runtime.get("routing_policy", "HYBRID_SELF_FIRST"),
    }
