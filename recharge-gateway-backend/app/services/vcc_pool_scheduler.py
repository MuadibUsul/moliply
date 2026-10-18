"""V2 VCC pool scheduler with region-aware selection and failure fuses."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RechargeState, RechargeTask, VccCard, VccStatus
from app.services import region_router

logger = logging.getLogger(__name__)


class NoAvailableVccError(Exception):
    code = "NO_ACTIVE_VCC_IN_POOL"


def acquire_card(db: Session, region: str) -> VccCard | None:
    minimum = region_router.min_balance_for(region)
    query = db.query(VccCard).filter(
        VccCard.status == VccStatus.ACTIVE,
        VccCard.available_balance >= minimum,
        VccCard.current_bind_count < VccCard.max_bind_count,
        VccCard.failed_count == 0,
    )
    if region == region_router.PH:
        query = query.filter(VccCard.ph_billing_address_json.isnot(None))
    return (
        query.order_by(VccCard.available_balance.desc(), VccCard.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )


def mark_success(db: Session, card: VccCard, region: str) -> None:
    cost = region_router.cost_usd_for(region)
    card.current_bind_count += 1
    card.available_balance = max(Decimal("0"), card.available_balance - cost)
    if card.current_bind_count >= card.max_bind_count:
        card.status = VccStatus.EXHAUSTED
    db.add(card)
    db.commit()


def suspend(db: Session, card: VccCard, reason: str = "") -> None:
    card.failed_count += 1
    card.status = VccStatus.SUSPENDED
    db.add(card)
    db.commit()
    logger.warning("VCC card id=%s suspended: %s", card.id, reason or "declined")


def mark_closed(db: Session, card: VccCard) -> None:
    card.status = VccStatus.CLOSED
    card.available_balance = Decimal("0")
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
            if card.status == VccStatus.ACTIVE and card.current_bind_count < card.max_bind_count
        ),
        "exhausted": [card.id for card in cards if card.status == VccStatus.EXHAUSTED],
        "suspended": [card.id for card in cards if card.status == VccStatus.SUSPENDED],
        "closed": sum(1 for card in cards if card.status == VccStatus.CLOSED),
        "available_balance_usd": float(sum((card.available_balance for card in cards), Decimal("0"))),
        "today_self": channels.get("SELF_VCC", 0),
        "today_upstream": channels.get("UPSTREAM", 0),
        "today_fallback": int(fallback),
        "routing_policy": region_router.runtime.get("routing_policy", "HYBRID_SELF_FIRST"),
    }
