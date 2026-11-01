"""Alipay Face-To-Face (当面付) adapter: RSA2 signed precreate + callback.

Alipay signs with the merchant's RSA private key over ISP-sorted parameters and
verifies with the Alipay platform public key. ``cryptography`` is used directly so
no official SDK is pulled in. Outbound calls are synchronous so the provider
matches the shared ``PaymentProvider`` interface.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings
from app.services import net_guard
from app.services import runtime_settings as runtime
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError
from app.services.payments.desensitize import OfficialPaymentGatewayMixin

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://openapi.alipay.com"
SUCCESS_STATES = {"TRADE_SUCCESS", "TRADE_FINISHED"}


def _format_pem(raw_key: str, is_private: bool) -> bytes:
    """Accept a PEM block or a bare base64 body copied out of the console."""
    cleaned = raw_key.strip().replace("\\n", "\n")
    if "-----BEGIN" in cleaned:
        return cleaned.encode("utf-8")
    header = "-----BEGIN PRIVATE KEY-----" if is_private else "-----BEGIN PUBLIC KEY-----"
    footer = "-----END PRIVATE KEY-----" if is_private else "-----END PUBLIC KEY-----"
    body = "\n".join(cleaned[index : index + 64] for index in range(0, len(cleaned), 64))
    return f"{header}\n{body}\n{footer}".encode("utf-8")


def _sign_content(params: dict[str, Any]) -> bytes:
    ordered = sorted(
        (str(name), str(value))
        for name, value in params.items()
        if value not in (None, "") and name not in {"sign", "sign_type"}
    )
    return "&".join(f"{name}={value}" for name, value in ordered).encode("utf-8")


class AlipayF2FProvider(OfficialPaymentGatewayMixin, PaymentProvider):
    name = "alipay_f2f"

    def __init__(self) -> None:
        self.app_id = runtime.get("alipay_app_id")
        self.api_base = runtime.get("alipay_api_base", settings.alipay_api_base) or DEFAULT_API_BASE
        self.notify_url = runtime.get("alipay_notify_url", settings.alipay_notify) or settings.alipay_notify
        private_key = runtime.get("alipay_app_private_key")
        alipay_public_key = runtime.get("alipay_public_key")
        try:
            net_guard.ensure_https(self.api_base)
        except ValueError as exc:
            raise PaymentProviderError("PAYMENT_INSECURE_URL", "支付 API 必须使用 HTTPS") from exc
        if not (self.app_id and private_key and alipay_public_key):
            raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "支付宝当面付参数尚未配置")
        try:
            self._private_key = serialization.load_pem_private_key(
                _format_pem(private_key, is_private=True), password=None
            )
            self._alipay_public_key = serialization.load_pem_public_key(
                _format_pem(alipay_public_key, is_private=False)
            )
        except Exception as exc:  # noqa: BLE001
            raise PaymentProviderError("PAYMENT_KEY_INVALID", "支付宝密钥格式不正确") from exc

    def _sign(self, content: bytes) -> str:
        signature = self._private_key.sign(content, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode("ascii")

    def _verify(self, content: bytes, signature_b64: str) -> bool:
        try:
            self._alipay_public_key.verify(
                base64.b64decode(signature_b64), content, padding.PKCS1v15(), hashes.SHA256()
            )
            return True
        except (InvalidSignature, ValueError):
            return False

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        expires_minutes = runtime.get_int("order_expire_minutes", settings.order_expire_minutes)
        biz_content = {
            "out_trade_no": order_no,
            "total_amount": f"{amount.quantize(Decimal('0.01'))}",
            "subject": self.build_safe_subject(order_no),
            "timeout_express": f"{expires_minutes}m",
        }
        params: dict[str, str] = {
            "app_id": self.app_id,
            "method": "alipay.trade.precreate",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": self.notify_url,
            "biz_content": json.dumps(biz_content, separators=(",", ":"), ensure_ascii=False),
        }
        params["sign"] = self._sign(_sign_content(params))

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{self.api_base.rstrip('/')}/gateway.do?charset=utf-8",
                    content="&".join(f"{key}={quote_plus(value)}" for key, value in params.items()).encode("utf-8"),
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付下单失败，请稍后重试") from exc

        body = payload.get("alipay_trade_precreate_response") or {}
        if str(body.get("code")) != "10000" or not body.get("qr_code"):
            logger.warning("Alipay precreate rejected: %s %s", body.get("msg"), body.get("sub_msg"))
            raise PaymentProviderError(
                "PAYMENT_CREATE_FAILED", str(body.get("sub_msg") or body.get("msg") or "支付下单失败")
            )
        return PaymentIntent(
            provider="alipay_f2f",
            qr_content=str(body["qr_code"]),
            trade_no=str(body.get("out_trade_no") or ""),
            expires_in=expires_minutes * 60,
            raw={"code": body.get("code")},
        )

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        signature_value = str(payload.get("sign") or signature or "")
        verified = bool(signature_value) and self._verify(_sign_content(payload), signature_value)
        app_id_matches = str(payload.get("app_id") or "") == str(self.app_id or "")
        status_value = str(payload.get("trade_status") or "").upper()
        try:
            amount = Decimal(str(payload.get("total_amount") or "0"))
        except Exception:  # noqa: BLE001
            amount = Decimal("0")
        return ParsedNotify(
            provider="alipay_f2f",
            order_no=str(payload.get("out_trade_no") or ""),
            amount=amount,
            trade_no=str(payload.get("trade_no") or ""),
            verified=verified and app_id_matches,
            success=status_value in SUCCESS_STATES,
            currency="CNY",
            raw={"status": status_value, "app_id_matches": app_id_matches},
        )

    def success_body(self) -> str:
        return "success"
