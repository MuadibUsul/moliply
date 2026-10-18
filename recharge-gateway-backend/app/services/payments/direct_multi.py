"""Direct ETH, USDC-ERC20 and BTC collection providers."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OrderStatus, utcnow
from app.services import direct_crypto, net_guard
from app.services import runtime_settings as runtime
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError


def _select_unique(db: Session, cny_amount: Decimal, coin: str) -> Decimal:
    existing = {
        Decimal(str(amount))
        for (amount,) in db.query(Order.payment_amount)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.payment_provider == coin.lower(),
            Order.payment_currency == direct_crypto.coin_currency(coin),
            Order.payment_amount.isnot(None),
            Order.expires_at > utcnow(),
        )
        .all()
    }
    for offset in range(100):
        candidate = direct_crypto.coin_amount(cny_amount, coin, offset)
        if candidate not in existing:
            return candidate
    raise PaymentProviderError("CRYPTO_AMOUNT_COLLISION", "当前订单较多，请稍后重试")


class _DirectMultiCoinProvider(PaymentProvider):
    coin = ""

    def __init__(self) -> None:
        if not direct_crypto.coin_configured(self.coin):
            raise PaymentProviderError("CRYPTO_NOT_CONFIGURED", f"{direct_crypto.coin_label(self.coin)} 收款尚未配置")
        self.address = direct_crypto.coin_address(self.coin)
        self.invoice_minutes = runtime.get_int("crypto_invoice_minutes", settings.crypto_invoice_minutes)

    def select_unique_amount(self, db: Session, cny_amount: Decimal) -> Decimal:
        return _select_unique(db, cny_amount, self.coin)

    def create_native(
        self,
        order_no: str,
        amount: Decimal,
        title: str,
        client_ip: str = "",
        payment_amount: Decimal | None = None,
    ) -> PaymentIntent:
        expected = payment_amount or direct_crypto.coin_amount(amount, self.coin)
        expires_at = utcnow() + timedelta(minutes=self.invoice_minutes)
        return PaymentIntent(
            provider=self.coin.lower(),
            qr_content=self.address,
            pay_url="",
            trade_no=f"{self.coin}-{order_no}",
            mock=False,
            expires_in=self.invoice_minutes * 60,
            payment_address=self.address,
            payment_amount=expected,
            payment_currency=direct_crypto.coin_currency(self.coin),
            network=direct_crypto.coin_network(self.coin),
            expires_at=expires_at.isoformat() + "Z",
            raw={
                "address": self.address,
                "amount": str(expected),
                "currency": direct_crypto.coin_currency(self.coin),
                "network": direct_crypto.coin_network(self.coin),
                "expires_at": expires_at.isoformat() + "Z",
            },
        )

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        raise PaymentProviderError("CRYPTO_USE_CHAIN_WATCHER", "直收币种使用链上监听确认")


class DirectEthereumProvider(_DirectMultiCoinProvider):
    def __init__(self, coin: str = "USDC_ERC20") -> None:
        if coin not in {"USDC_ERC20", "ETH"}:
            raise PaymentProviderError("CRYPTO_COIN_UNSUPPORTED", "不支持的以太坊币种")
        self.coin = coin
        super().__init__()
        try:
            net_guard.ensure_https(runtime.get("etherscan_api_base", settings.etherscan_api_base))
        except ValueError as exc:
            raise PaymentProviderError("CRYPTO_CONFIG_INVALID", "Etherscan API 必须使用 HTTPS") from exc


class DirectBitcoinProvider(_DirectMultiCoinProvider):
    coin = "BTC"

    def __init__(self) -> None:
        super().__init__()
        try:
            net_guard.ensure_https(runtime.get("btc_api_base", settings.btc_api_base))
        except ValueError as exc:
            raise PaymentProviderError("CRYPTO_CONFIG_INVALID", "BTC API 必须使用 HTTPS") from exc
