from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class PaymentProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PaymentIntent:
    provider: str
    qr_content: str
    pay_url: str = ""
    trade_no: str = ""
    mock: bool = False
    expires_in: int = 900
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedNotify:
    provider: str
    order_no: str
    amount: Decimal
    trade_no: str
    verified: bool
    success: bool
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider:
    name = "base"

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        raise NotImplementedError

    def parse_notify(self, payload: dict[str, Any]) -> ParsedNotify:
        raise NotImplementedError

    def success_body(self) -> str:
        return "success"
