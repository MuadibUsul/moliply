from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from app.config import settings
from app.services import runtime_settings as runtime
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError
from app.services.payments.ltzf import LtzfProvider
from app.services.payments.mock import MockPaymentProvider
from app.services.payments.nowpayments import NowPaymentsProvider
from app.services.payments.xunhupay import XunhupayProvider

MASKED_TITLE_POOL = [
    "模力补给-数字商品订单-{suffix}",
    "MOLIPLY-软件订阅服务-{suffix}",
    "模力补给-数字权益卡券-{suffix}",
]

FORBIDDEN_TITLE_WORDS = [
    "chatgpt",
    "claude",
    "openai",
    "代充",
    "虚拟卡",
    "外币",
    "vpn",
    "代理节点",
    "翻墙",
]


def masked_order_title(order_no: str) -> str:
    """Payment-provider safe order title, shared by both gateways and tests."""
    suffix = (order_no or "0000")[-4:]
    index = int(hashlib.sha256((order_no or "").encode("utf-8")).hexdigest()[:8], 16) % len(MASKED_TITLE_POOL)
    title = MASKED_TITLE_POOL[index].format(suffix=suffix)
    lowered = title.lower()
    if any(word.lower() in lowered for word in FORBIDDEN_TITLE_WORDS):
        # Defensive: the pool is curated, this should never trigger.
        title = f"模力补给-数字商品订单-{suffix}"
    return title


def get_callback_provider(name: str) -> PaymentProvider:
    """Build a signature-verifying provider for an asynchronous callback.

    Callbacks are verified with the live gateway class even when the storefront
    currently runs in mock mode, so a misconfigured environment cannot silently
    accept forged notifications.
    """
    provider = (name or "").lower()
    if provider == "ltzf":
        return LtzfProvider(channel="wechat")
    if provider == "xunhupay":
        return XunhupayProvider()
    if provider == "nowpayments":
        return NowPaymentsProvider()
    return MockPaymentProvider(provider or "mock")


def get_provider(name: str) -> PaymentProvider:
    provider = (name or "").lower()
    payment_mode = runtime.get("payment_mode", "mock").lower()
    gateway = runtime.get("payment_gateway", "ltzf").lower()
    if provider in {"crypto", "nowpayments"}:
        if not runtime.get_bool("crypto_enabled"):
            raise PaymentProviderError("CRYPTO_DISABLED", "加密货币支付暂未启用")
        if runtime.get("crypto_provider", "nowpayments").lower() == "mock":
            return MockPaymentProvider("crypto")
        return NowPaymentsProvider()
    if provider in {"wechat", "wxpay", "ltzf", "lanttu"}:
        if payment_mode != "live":
            return MockPaymentProvider("wechat")
        if gateway == "xunhupay":
            return XunhupayProvider()
        return LtzfProvider(channel="wechat")
    if provider in {"alipay", "ltzf_alipay"}:
        if payment_mode != "live":
            return MockPaymentProvider("alipay")
        if gateway == "xunhupay":
            return XunhupayProvider()
        return LtzfProvider(channel="alipay")
    if provider == "xunhupay":
        if payment_mode != "live":
            return MockPaymentProvider("xunhupay")
        return XunhupayProvider()
    if provider == "mock":
        return MockPaymentProvider("mock")
    raise PaymentProviderError("UNSUPPORTED_PROVIDER", f"不支持的支付方式：{name}")


__all__ = [
    "MASKED_TITLE_POOL",
    "ParsedNotify",
    "PaymentIntent",
    "PaymentProvider",
    "PaymentProviderError",
    "get_callback_provider",
    "get_provider",
    "masked_order_title",
]
