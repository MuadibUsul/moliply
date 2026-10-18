from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.config import settings
from app.services import runtime_settings as runtime
from app.services.payments.guards import mock_payments_allowed
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError


class MockPaymentProvider(PaymentProvider):
    """Development-only provider that renders a QR pointing at our pay page."""

    name = "mock"

    def __init__(self, channel: str) -> None:
        self.channel = channel

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        if not mock_payments_allowed():
            raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "支付通道尚未配置，请联系客服")
        pay_url = f"{settings.public_store_url.rstrip('/')}/pay/{order_no}?mock=1&channel={self.channel}"
        return PaymentIntent(
            provider=self.channel,
            qr_content=pay_url,
            pay_url=pay_url,
            trade_no=f"MOCK-{order_no}",
            mock=True,
            expires_in=runtime.get_int("order_expire_minutes", settings.order_expire_minutes) * 60,
        )

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        return ParsedNotify(
            provider=self.channel,
            order_no=str(payload.get("order_no", "")),
            amount=Decimal(str(payload.get("amount", "0"))),
            trade_no=str(payload.get("trade_no", "")),
            verified=True,
            success=True,
            raw={},
        )
