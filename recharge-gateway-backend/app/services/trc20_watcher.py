"""TronGrid watcher for the self-hosted USDT-TRC20 payment channel."""

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
from app.services.payments.trc20 import TRON_ADDRESS_RE

logger = logging.getLogger(__name__)


def _config() -> tuple[str, str, str, str] | None:
    if not runtime.get_bool("crypto_enabled", settings.crypto_enabled):
        return None
    if runtime.get("crypto_provider", settings.crypto_provider).lower() != "usdt_trc20":
        return None
    wallet = runtime.get("usdt_trc20_wallet_address", settings.usdt_trc20_wallet_address).strip()
    contract = runtime.get("usdt_trc20_contract", settings.usdt_trc20_contract).strip()
    api_base = runtime.get("tron_api_base", settings.tron_api_base).strip()
    api_key = runtime.get("tron_api_key", settings.tron_api_key).strip()
    if not wallet or not TRON_ADDRESS_RE.match(wallet) or not TRON_ADDRESS_RE.match(contract):
        return None
    try:
        net_guard.ensure_https(api_base)
    except ValueError:
        logger.warning("TRC20 watcher: TronGrid API base must use HTTPS")
        return None
    return wallet, contract, api_base, api_key


def _fetch_transfers(api_base: str, wallet: str, contract: str, api_key: str, min_ts: int, max_ts: int) -> list[dict[str, Any]]:
    params = {
        "only_to": "true",
        "only_confirmed": "true",
        "contract_address": contract,
        "min_timestamp": min_ts,
        "max_timestamp": max_ts,
        "limit": 200,
    }
    headers = {"Accept": "application/json"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    with httpx.Client(timeout=25.0) as client:
        response = client.get(
            f"{api_base.rstrip('/')}/v1/accounts/{wallet}/transactions/trc20",
            params=params,
            headers=headers,
        )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, list) else []


def _transfer(item: dict[str, Any], wallet: str, contract: str) -> dict[str, Any] | None:
    token = item.get("token_info") or {}
    if str(token.get("address") or "") != contract:
        return None
    if str(item.get("to") or "") != wallet:
        return None
    if str(item.get("type") or "Transfer") != "Transfer":
        return None
    if item.get("confirmed") is False:
        return None
    txid = str(item.get("transaction_id") or item.get("transactionHash") or "")
    if not txid:
        return None
    try:
        decimals = int(token.get("decimals", 6))
        amount = Decimal(str(item.get("value") or "0")) / (Decimal(10) ** decimals)
        timestamp_ms = int(item.get("block_timestamp") or 0)
        received_at = datetime.utcfromtimestamp(timestamp_ms / 1000) if timestamp_ms else None
    except (TypeError, ValueError, InvalidOperation, OSError):
        return None
    return {
        "txid": txid,
        "amount": amount,
        "received_at": received_at,
        "from": str(item.get("from") or ""),
        "contract": contract,
    }


def scan_pending_orders(db: Session) -> int:
    config = _config()
    if config is None:
        return 0
    wallet, contract, api_base, api_key = config
    now = utcnow()
    grace = timedelta(minutes=max(1, runtime.get_int("crypto_payment_grace_minutes", settings.crypto_payment_grace_minutes)))
    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.payment_provider == "usdt_trc20",
            Order.payment_address == wallet,
            Order.payment_amount.isnot(None),
            Order.created_at >= now - timedelta(hours=24),
            or_(Order.expires_at.is_(None), Order.expires_at > now - grace),
        )
        .order_by(Order.created_at.asc())
        .all()
    )
    if not pending:
        return 0

    start = min(order.created_at for order in pending) - timedelta(seconds=120)
    end = now + timedelta(seconds=60)
    try:
        items = _fetch_transfers(
            api_base,
            wallet,
            contract,
            api_key,
            int(start.replace(tzinfo=timezone.utc).timestamp() * 1000),
            int(end.replace(tzinfo=timezone.utc).timestamp() * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("TRC20 watcher fetch failed: %s", exc.__class__.__name__)
        return 0

    transfers = [transfer for item in items if (transfer := _transfer(item, wallet, contract))]
    if not transfers:
        return 0

    # Import here to avoid a module cycle with the order service.
    from app.services import orders as order_service

    matched = 0
    used_txids: set[str] = set()
    for order in pending:
        expected = Decimal(str(order.payment_amount))
        for transfer in transfers:
            if transfer["txid"] in used_txids:
                continue
            if abs(transfer["amount"] - expected) > Decimal("0.000001"):
                continue
            received_at = transfer["received_at"]
            if received_at and received_at < order.created_at - timedelta(seconds=120):
                continue
            if received_at and order.expires_at and received_at > order.expires_at + grace:
                continue
            existing = (
                db.query(PaymentEvent)
                .filter(
                    PaymentEvent.provider == "usdt_trc20",
                    PaymentEvent.event_id == transfer["txid"],
                )
                .first()
            )
            if existing:
                used_txids.add(transfer["txid"])
                break
            db.add(
                PaymentEvent(
                    provider="usdt_trc20",
                    event_id=transfer["txid"],
                    order_no=order.order_no,
                    amount=transfer["amount"],
                    verified=True,
                    raw_json={
                        "network": "TRC20",
                        "from": transfer["from"],
                        "contract": transfer["contract"],
                    },
                )
            )
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                continue
            try:
                paid = order_service.mark_order_paid(db, order, "usdt_trc20", transfer["txid"])
                if not paid:
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("TRC20 payment could not settle order: %s", exc.__class__.__name__)
                continue
            used_txids.add(transfer["txid"])
            matched += 1
            logger.info("TRC20 payment matched order %s tx=%s", order.order_no, transfer["txid"])
            break
    return matched
