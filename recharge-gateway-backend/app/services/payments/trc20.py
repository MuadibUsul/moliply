"""Self-hosted USDT-TRC20 direct collection.

The customer sends USDT (TRC20) directly to the merchant wallet; a background
TronGrid watcher matches the unique invoice amount and marks the order paid.
No private key is stored on the server and no third-party checkout is used.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OrderStatus, utcnow
from app.services import net_guard
from app.services import runtime_settings as runtime
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError

TRON_ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
DEFAULT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class DirectTrc20Provider(PaymentProvider):
    name = "usdt_trc20"

    def __init__(self) -> None:
        self.wallet_address = runtime.get(
            "usdt_trc20_wallet_address",
            settings.usdt_trc20_wallet_address,
        ).strip()
        self.contract = runtime.get(
            "usdt_trc20_contract",
            settings.usdt_trc20_contract,
        ).strip() or DEFAULT_CONTRACT
        self.api_base = runtime.get("tron_api_base", settings.tron_api_base)
        self.api_key = runtime.get("tron_api_key", settings.tron_api_key)
        self.rate = runtime.get_float("crypto_cny_per_usd", settings.crypto_cny_per_usd)
        self.invoice_minutes = runtime.get_int("crypto_invoice_minutes", settings.crypto_invoice_minutes)
        if not self.wallet_address:
            raise PaymentProviderError("CRYPTO_NOT_CONFIGURED", "USDT-TRC20 收款地址尚未配置")
        if not TRON_ADDRESS_RE.match(self.wallet_address) or not TRON_ADDRESS_RE.match(self.contract):
            raise PaymentProviderError("CRYPTO_CONFIG_INVALID", "TRON 地址或合约地址格式不正确")
        try:
            net_guard.ensure_https(self.api_base)
        except ValueError as exc:
            raise PaymentProviderError("CRYPTO_CONFIG_INVALID", "TronGrid API 必须使用 HTTPS") from exc
        if self.rate <= 0:
            raise PaymentProviderError("CRYPTO_CONFIG_INVALID", "CNY/USD 汇率必须大于 0")

    def select_unique_amount(self, db: Session, cny_amount: Decimal) -> Decimal:
        base = (Decimal(str(cny_amount)) / Decimal(str(self.rate))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if base < Decimal("0.01"):
            base = Decimal("0.01")
        existing = {
            Decimal(str(amount))
            for (amount,) in db.query(Order.payment_amount)
            .filter(
                Order.status == OrderStatus.PENDING,
                Order.payment_provider == self.name,
                Order.payment_currency == "USDT",
                Order.payment_amount.isnot(None),
                Order.expires_at > utcnow(),
            )
            .all()
        }
        for offset in range(100):
            candidate = (base + Decimal(offset) / Decimal("100")).quantize(Decimal("0.01"))
            if candidate not in existing:
                return candidate
        raise PaymentProviderError("CRYPTO_AMOUNT_COLLISION", "当前订单较多，请稍后重试")

    def create_native(
        self,
        order_no: str,
        amount: Decimal,
        title: str,
        client_ip: str = "",
        payment_amount: Decimal | None = None,
    ) -> PaymentIntent:
        expected = payment_amount or self.select_unique_amount_from_rate(amount)
        expires_at = utcnow() + timedelta(minutes=self.invoice_minutes)
        return PaymentIntent(
            provider=self.name,
            qr_content=self.wallet_address,
            pay_url="",
            trade_no=f"TRC20-{order_no}",
            mock=False,
            expires_in=self.invoice_minutes * 60,
            payment_address=self.wallet_address,
            payment_amount=expected,
            payment_currency="USDT",
            network="TRC20",
            expires_at=expires_at.isoformat() + "Z",
            raw={
                "address": self.wallet_address,
                "amount": str(expected),
                "currency": "USDT",
                "network": "TRC20",
                "contract": self.contract,
                "expires_at": expires_at.isoformat() + "Z",
            },
        )

    def select_unique_amount_from_rate(self, cny_amount: Decimal) -> Decimal:
        base = (Decimal(str(cny_amount)) / Decimal(str(self.rate))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        return max(Decimal("0.01"), base)

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        raise PaymentProviderError("CRYPTO_USE_CHAIN_WATCHER", "USDT-TRC20 使用链上监听确认")
