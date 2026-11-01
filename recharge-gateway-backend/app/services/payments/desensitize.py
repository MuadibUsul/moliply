"""Compliant billing subject for official (WeChat Pay / Alipay) channels.

Both official channels run risk control against the product name carried in the
payment request. Catalogue titles mention ChatGPT/Claude/代充/虚拟卡, which the
merchant category does not cover, so every official gateway receives one fixed
descriptor plus the order number instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


def subject_prefix() -> str:
    """Configured prefix, falling back to the built-in default."""
    from app.services import runtime_settings as runtime

    return runtime.get("official_payment_subject_prefix") or DEFAULT_SUBJECT_PREFIX


DEFAULT_SUBJECT_PREFIX = "MOLIPLY-技术开发与算力服务"

# WeChat V3 rejects ``description`` above 127 bytes; Alipay ``subject`` above 256.
MAX_SUBJECT_BYTES = 127


@dataclass(frozen=True)
class DesensitizedBillingSubject:
    order_no: str
    prefix: str = ""

    def render(self) -> str:
        prefix = self.prefix or subject_prefix()
        subject = f"{prefix}-{self.order_no}"
        encoded = subject.encode("utf-8")
        if len(encoded) > MAX_SUBJECT_BYTES:
            # Only lose prefix bytes, the order number must survive intact.
            keep = MAX_SUBJECT_BYTES - len(self.order_no) - 1
            subject = f"{prefix.encode('utf-8')[:keep].decode('utf-8', 'ignore')}-{self.order_no}"
        return subject


class OfficialPaymentGatewayMixin:
    """Shared desensitisation and amount helpers for official gateways."""

    @staticmethod
    def build_safe_subject(order_no: str) -> str:
        return DesensitizedBillingSubject(order_no=order_no).render()

    @staticmethod
    def to_cents(amount: Decimal) -> int:
        return int((amount * Decimal("100")).quantize(Decimal("1")))
