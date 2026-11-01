"""Blockstream watcher for direct BTC payments."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OrderStatus, PaymentEvent, utcnow
from app.services import net_guard
from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)


def _config() -> tuple[str, str, int] | None:
    if not runtime.get_bool("crypto_enabled", settings.crypto_enabled):
        return None
    if runtime.get("crypto_provider", settings.crypto_provider).lower() != "usdt_trc20":
        return None
    address = runtime.get("btc_wallet_address", settings.btc_wallet_address).strip()
    api_base = runtime.get("btc_api_base", settings.btc_api_base).strip()
    if not address or not api_base:
        return None
    try:
        net_guard.ensure_https(api_base)
    except ValueError:
        return None
    return address, api_base, runtime.get_int("btc_confirmations", settings.btc_confirmations)


def _get_json(url: str) -> Any:
    with httpx.Client(timeout=25.0) as client:
        response = client.get(url)
    response.raise_for_status()
    return response.json()


def scan_pending_orders(db: Session) -> int:
    config = _config()
    if config is None:
        return 0
    address, api_base, required = config
    now = utcnow()
    grace = timedelta(minutes=max(1, runtime.get_int("crypto_payment_grace_minutes", settings.crypto_payment_grace_minutes)))
    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.payment_provider == "btc",
            Order.payment_address == address,
            Order.payment_amount.isnot(None),
            Order.created_at >= now - timedelta(hours=24),
            or_(Order.expires_at.is_(None), Order.expires_at > now - grace),
        )
        .order_by(Order.created_at.asc())
        .all()
    )
    if not pending:
        return 0

    try:
        tip = int(_get_json(f"{api_base.rstrip('/')}/blocks/tip/height"))
        txs = _get_json(f"{api_base.rstrip('/')}/address/{address}/txs")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blockstream watcher fetch failed: %s", exc.__class__.__name__)
        return 0
    if not isinstance(txs, list):
        return 0

    from app.services import orders as order_service

    matched = 0
    for order in pending:
        expected = Decimal(str(order.payment_amount))
        for tx in txs:
            status = tx.get("status") or {}
            if not status.get("confirmed"):
                continue
            height = int(status.get("block_height") or 0)
            if height <= 0 or tip - height + 1 < required:
                continue
            vouts = tx.get("vout") or []
            received = sum(
                Decimal(str(vout.get("value") or "0"))
                for vout in vouts
                if str(vout.get("scriptpubkey_address") or "") == address
            ) / Decimal(100_000_000)
            if abs(received - expected) > Decimal("0.00000001"):
                continue
            try:
                received_at = datetime.fromtimestamp(int(status.get("block_time") or 0), tz=timezone.utc).replace(tzinfo=None)
            except (TypeError, ValueError, OSError):
                received_at = None
            if received_at and received_at < order.created_at - timedelta(seconds=120):
                continue
            if received_at and order.expires_at and received_at > order.expires_at + grace:
                continue
            txid = str(tx.get("txid") or "")
            if not txid:
                continue
            existing = (
                db.query(PaymentEvent)
                .filter(PaymentEvent.provider == "btc", PaymentEvent.event_id == txid)
                .first()
            )
            if existing:
                break
            db.add(
                PaymentEvent(
                    provider="btc",
                    event_id=txid,
                    order_no=order.order_no,
                    amount=received,
                    verified=True,
                    raw_json={"network": "BTC", "confirmations": tip - height + 1},
                )
            )
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                continue
            try:
                paid = order_service.mark_order_paid(db, order, "btc", txid)
                if not paid:
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("BTC payment could not settle order: %s", exc.__class__.__name__)
                continue
            matched += 1
            logger.info("BTC payment matched order %s tx=%s", order.order_no, txid)
            break
    return matched
