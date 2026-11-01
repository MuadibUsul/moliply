from __future__ import annotations

from app.config import settings
from app.services import runtime_settings as runtime
from app.services.payments.alipay_f2f import AlipayF2FProvider
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError
from app.services.payments.desensitize import (
    DEFAULT_SUBJECT_PREFIX,
    DesensitizedBillingSubject,
    OfficialPaymentGatewayMixin,
)
from app.services.payments.guards import mock_payments_allowed
from app.services.payments.mock import MockPaymentProvider
from app.services.payments.nowpayments import NowPaymentsProvider
from app.services.payments.trc20 import DirectTrc20Provider
from app.services.payments.wechat_native import WechatNativeProvider

# Official channels are served by the merchant account itself, so the descriptor
# is one fixed compliant sentence plus the order number rather than the catalogue
# product name. Kept as a pool only for callers that still import it.
MASKED_TITLE_POOL = [f"{DEFAULT_SUBJECT_PREFIX}-{{suffix}}"]

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

OFFICIAL_PROVIDERS = {"wechat", "wxpay", "alipay"}


def masked_order_title(order_no: str) -> str:
    """Exact descriptor sent to every official gateway."""
    return DesensitizedBillingSubject(order_no=order_no or "0000").render()


def official_descriptor(order_no: str) -> str:
    return DesensitizedBillingSubject(order_no=order_no or "0000").render()


def official_payment_ready() -> bool:
    """True when the selected official gateway has all of its credentials."""
    gateway = runtime.get("payment_gateway", "wechat").lower()
    if gateway == "alipay":
        return bool(
            runtime.get("alipay_app_id")
            and runtime.get("alipay_app_private_key")
            and runtime.get("alipay_public_key")
        )
    if gateway == "wechat":
        return bool(
            runtime.get("wechat_app_id")
            and runtime.get("wechat_mch_id")
            and runtime.get("wechat_mch_serial_no")
            and runtime.get("wechat_mch_private_key")
            and runtime.get("wechat_apiv3_key")
        )
    return False


def get_callback_provider(name: str) -> PaymentProvider:
    """Build a signature-verifying provider for an asynchronous callback.

    Callbacks are verified with the live gateway class even when the storefront
    currently runs in mock mode, so a misconfigured environment cannot silently
    accept forged notifications.
    """
    provider = (name or "").lower()
    if provider in {"alipay", "alipay_f2f"}:
        return AlipayF2FProvider()
    if provider in {"wechat", "wechat_native", "wxpay"}:
        return WechatNativeProvider()
    if provider == "nowpayments":
        return NowPaymentsProvider()
    return MockPaymentProvider(provider or "mock")


def get_provider(name: str) -> PaymentProvider:
    provider = (name or "").lower()
    payment_mode = runtime.get("payment_mode", "mock").lower()
    gateway = runtime.get("payment_gateway", "wechat").lower()
    if provider in {"crypto", "nowpayments", "usdt_trc20"}:
        if not runtime.get_bool("crypto_enabled"):
            raise PaymentProviderError("CRYPTO_DISABLED", "加密货币支付暂未启用")
        mode = runtime.get("crypto_provider", "nowpayments").lower()
        if mode == "mock":
            return MockPaymentProvider("crypto")
        if mode == "usdt_trc20":
            return DirectTrc20Provider()
        return NowPaymentsProvider()
    if provider in OFFICIAL_PROVIDERS:
        if payment_mode != "live":
            return MockPaymentProvider("wechat" if provider != "alipay" else "alipay")
        # "wechat"/"alipay" name the app the customer scans with; the gateway
        # switch decides which official merchant account serves it.
        if gateway == "alipay":
            return AlipayF2FProvider()
        if gateway == "wechat":
            return WechatNativeProvider()
        raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "官方支付网关尚未选择")
    if provider == "mock":
        return MockPaymentProvider("mock")
    raise PaymentProviderError("UNSUPPORTED_PROVIDER", f"不支持的支付方式：{name}")


__all__ = [
    "AlipayF2FProvider",
    "DirectTrc20Provider",
    "FORBIDDEN_TITLE_WORDS",
    "MASKED_TITLE_POOL",
    "OfficialPaymentGatewayMixin",
    "ParsedNotify",
    "PaymentIntent",
    "PaymentProvider",
    "PaymentProviderError",
    "WechatNativeProvider",
    "get_callback_provider",
    "get_provider",
    "masked_order_title",
    "mock_payments_allowed",
    "official_descriptor",
    "official_payment_ready",
]
