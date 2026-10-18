"""NOWPayments hosted invoice adapter (USDT / USDC / BTC / ETH).

Merchant setup:
1. create a NOWPayments merchant account and API key;
2. set the IPN secret and callback URL;
3. enable USDT, USDC, BTC and ETH in the merchant dashboard;
4. set CRYPTO_ENABLED=true.

The invoice is priced in USD converted from the CNY order amount using
CRYPTO_CNY_PER_USD. The hosted invoice lets the payer choose the enabled coin.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from app.config import settings
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError

logger = logging.getLogger(__name__)


def ipn_signature(payload: dict[str, Any], secret: str) -> str:
    """NOWPayments IPN signature: HMAC-SHA512 over sorted compact JSON."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha512).hexdigest()


class NowPaymentsProvider(PaymentProvider):
    name = "crypto"

    def __init__(self) -> None:
        if not settings.nowpayments_api_key or not settings.nowpayments_ipn_secret:
            raise PaymentProviderError("CRYPTO_NOT_CONFIGURED", "加密货币支付商户参数尚未配置")

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        if not settings.crypto_enabled:
            raise PaymentProviderError("CRYPTO_DISABLED", "加密货币支付暂未启用")
        rate = Decimal(str(settings.crypto_cny_per_usd or 7.2))
        usd_amount = (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if usd_amount < Decimal("0.50"):
            usd_amount = Decimal("0.50")
        payload = {
            "price_amount": float(usd_amount),
            "price_currency": "usd",
            "order_id": order_no,
            "order_description": title,
            "ipn_callback_url": settings.nowpayments_ipn,
            "success_url": f"{settings.public_store_url.rstrip('/')}/pay/{order_no}?provider=crypto",
            "cancel_url": f"{settings.public_store_url.rstrip('/')}/pay/{order_no}?provider=crypto",
            "is_fixed_rate": True,
            "is_fee_paid_by_user": False,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{settings.nowpayments_api_base.rstrip('/')}/v1/invoice",
                    headers={"x-api-key": settings.nowpayments_api_key, "Content-Type": "application/json"},
                    json=payload,
                )
            if response.status_code >= 400:
                logger.warning("NOWPayments invoice failed with status %s", response.status_code)
                raise PaymentProviderError("CRYPTO_CREATE_FAILED", "加密货币下单失败，请稍后重试")
            data = response.json()
        except PaymentProviderError:
            raise
        except Exception as exc:
            logger.warning("NOWPayments invoice request failed: %s", exc.__class__.__name__)
            raise PaymentProviderError("CRYPTO_CREATE_FAILED", "加密货币下单失败，请稍后重试") from exc

        invoice_url = str(data.get("invoice_url") or "")
        if not invoice_url:
            raise PaymentProviderError("CRYPTO_CREATE_FAILED", "加密货币支付通道未返回收银台地址")
        return PaymentIntent(
            provider="crypto",
            qr_content=invoice_url,
            pay_url=invoice_url,
            trade_no=str(data.get("id") or data.get("invoice_id") or ""),
            mock=False,
            expires_in=settings.order_expire_minutes * 60,
            raw={"invoice_id": data.get("id"), "price_currency": "usd"},
        )

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        expected = ipn_signature(payload, settings.nowpayments_ipn_secret) if settings.nowpayments_ipn_secret else ""
        verified = bool(signature) and bool(expected) and hmac.compare_digest(expected, signature)
        status = str(payload.get("payment_status") or "").lower()
        amount = Decimal(str(payload.get("price_amount") or "0"))
        return ParsedNotify(
            provider="crypto",
            order_no=str(payload.get("order_id") or ""),
            amount=amount,
            trade_no=str(payload.get("payment_id") or payload.get("invoice_id") or payload.get("id") or ""),
            verified=verified,
            success=status == "finished",
            skip_amount_check=True,
            raw={"status": status},
        )

    def success_body(self) -> str:
        return "OK"
