"""Etherscan watcher for direct ETH and USDC-ERC20 payments."""

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
from app.services import direct_crypto, net_guard
from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)


def _config() -> tuple[str, str, int] | None:
    if not runtime.get_bool("crypto_enabled", settings.crypto_enabled):
        return None
    if runtime.get("crypto_provider", settings.crypto_provider).lower() != "usdt_trc20":
        return None
    api_base = runtime.get("etherscan_api_base", settings.etherscan_api_base).strip()
    api_key = runtime.get("etherscan_api_key", settings.etherscan_api_key).strip()
    if not api_key or not api_base:
        return None
    try:
        net_guard.ensure_https(api_base)
    except ValueError:
        return None
    return api_base, api_key, runtime.get_int("eth_confirmations", settings.eth_confirmations)


def _request(api_base: str, params: dict[str, str]) -> list[dict[str, Any]]:
    with httpx.Client(timeout=25.0) as client:
        response = client.get(api_base, params=params)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status")) != "1":
        return []
    result = payload.get("result")
    return result if isinstance(result, list) else []


def _current_block(api_base: str, api_key: str) -> int | None:
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                api_base,
                params={"chainid": "1", "module": "proxy", "action": "eth_blockNumber", "apikey": api_key},
            )
        response.raise_for_status()
        return int(response.json().get("result", "0x0"), 16)
    except Exception:  # noqa: BLE001
        return None


def _confirmations(item: dict[str, Any], current_block: int | None) -> int:
    try:
        if item.get("confirmations") not in (None, ""):
            return int(item["confirmations"])
        if current_block is not None:
            return max(0, current_block - int(item.get("blockNumber") or 0) + 1)
    except (TypeError, ValueError):
        pass
    return 0


def _amount(item: dict[str, Any], token: bool) -> Decimal | None:
    try:
        decimals = int(item.get("tokenDecimal", 18)) if token else 18
        return Decimal(str(item.get("value") or "0")) / (Decimal(10) ** decimals)
    except (TypeError, ValueError, InvalidOperation):
        return None


def scan_pending_orders(db: Session) -> int:
    config = _config()
    if config is None:
        return 0
    api_base, api_key, required = config
    now = utcnow()
    grace = timedelta(minutes=max(1, runtime.get_int("crypto_payment_grace_minutes", settings.crypto_payment_grace_minutes)))
    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.payment_provider.in_(["eth", "usdc_erc20"]),
            Order.payment_address.isnot(None),
            Order.payment_amount.isnot(None),
            Order.created_at >= now - timedelta(hours=24),
            or_(Order.expires_at.is_(None), Order.expires_at > now - grace),
        )
        .order_by(Order.created_at.asc())
        .all()
    )
    if not pending:
        return 0

    current_block = None
    matched = 0
    grouped: dict[tuple[str, str], list[Order]] = {}
    for order in pending:
        grouped.setdefault((order.payment_address or "", order.payment_provider or ""), []).append(order)

    from app.services import orders as order_service

    for (address, provider), orders in grouped.items():
        token = provider == "usdc_erc20"
        params = {
            "chainid": "1",
            "module": "account",
            "action": "tokentx" if token else "txlist",
            "address": address,
            "startblock": "0",
            "endblock": "99999999",
            "sort": "asc",
            "apikey": api_key,
        }
        if token:
            params["contractaddress"] = runtime.get("usdc_erc20_contract", settings.usdc_erc20_contract)
        try:
            items = _request(api_base, params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Etherscan watcher fetch failed: %s", exc.__class__.__name__)
            continue
        if token:
            items = [item for item in items if str(item.get("to") or "").lower() == address.lower()]
        else:
            items = [
                item
                for item in items
                if str(item.get("to") or "").lower() == address.lower() and str(item.get("isError", "0")) != "1"
            ]
        if not items:
            continue
        if current_block is None:
            current_block = _current_block(api_base, api_key)
        for order in orders:
            expected = Decimal(str(order.payment_amount))
            for item in items:
                if _confirmations(item, current_block) < required:
                    continue
                amount = _amount(item, token)
                if amount is None or abs(amount - expected) > Decimal("0.00000001"):
                    continue
                try:
                    received_at = datetime.fromtimestamp(int(item.get("timeStamp") or 0), tz=timezone.utc).replace(tzinfo=None)
                except (TypeError, ValueError, OSError):
                    received_at = None
                if received_at and received_at < order.created_at - timedelta(seconds=120):
                    continue
                if received_at and order.expires_at and received_at > order.expires_at + grace:
                    continue
                txid = str(item.get("hash") or "")
                if not txid:
                    continue
                existing = (
                    db.query(PaymentEvent)
                    .filter(PaymentEvent.provider == provider, PaymentEvent.event_id == txid)
                    .first()
                )
                if existing:
                    break
                db.add(
                    PaymentEvent(
                        provider=provider,
                        event_id=txid,
                        order_no=order.order_no,
                        amount=amount,
                        verified=True,
                        raw_json={"network": order.payment_network or "ETH", "from": str(item.get("from") or "")},
                    )
                )
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    continue
                try:
                    paid = order_service.mark_order_paid(db, order, provider, txid)
                    if not paid:
                        db.commit()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    logger.warning("EVM payment could not settle order: %s", exc.__class__.__name__)
                    continue
                matched += 1
                logger.info("EVM payment matched order %s tx=%s", order.order_no, txid)
                break
    return matched
