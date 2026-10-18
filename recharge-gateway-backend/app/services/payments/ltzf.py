"""Lanttu Pay (蓝兔支付) native QR adapter.

The merchant signature has historically been an MD5 over sorted parameters
plus the merchant key. Confirm the exact field list against the version of the
Lanttu documentation enabled for your merchant account before going live; the
signing helper is isolated so an API revision only touches this file.
"""

from __future__ import annotations

import hashlib
import time
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError


def _sign(params: dict[str, Any], key: str) -> str:
    filtered = {
        name: str(value)
        for name, value in params.items()
        if name != "sign" and value is not None and str(value) != ""
    }
    joined = "&".join(f"{name}={filtered[name]}" for name in sorted(filtered))
    return hashlib.md5(f"{joined}&key={key}".encode("utf-8")).hexdigest().upper()


class LtzfProvider(PaymentProvider):
    name = "ltzf"

    def __init__(self, channel: str = "wechat") -> None:
        self.channel = channel
        if not (settings.ltzf_mch_id and settings.ltzf_key):
            raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "蓝兔支付商户参数尚未配置")

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        params: dict[str, Any] = {
            "mch_id": settings.ltzf_mch_id,
            "app_id": settings.ltzf_app_id,
            "out_trade_no": order_no,
            "total_fee": f"{amount:.2f}",
            "body": title,
            "notify_url": settings.ltzf_notify,
            "timestamp": str(int(time.time())),
            "attach": "moliply",
        }
        if client_ip:
            params["client_ip"] = client_ip
        params["sign"] = _sign(params, settings.ltzf_key)
        path = "/api/wxpay/native" if self.channel == "wechat" else "/api/alipay/native"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{settings.ltzf_api_base.rstrip('/')}{path}",
                    data=params,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付下单失败，请稍后重试") from exc

        data = payload.get("data") or {}
        code_value = payload.get("code")
        if code_value not in (0, 200, "0", "200", None):
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", str(payload.get("msg") or "支付下单失败"))
        qr_content = data.get("code_url") or data.get("qr_code") or data.get("pay_url") or ""
        if not qr_content:
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付通道未返回二维码内容")
        return PaymentIntent(
            provider=f"ltzf_{self.channel}",
            qr_content=str(qr_content),
            pay_url=str(data.get("pay_url") or ""),
            trade_no=str(data.get("trade_no") or data.get("order_no") or ""),
            expires_in=settings.order_expire_minutes * 60,
            raw={"code": code_value},
        )

    def parse_notify(self, payload: dict[str, Any]) -> ParsedNotify:
        received_sign = str(payload.get("sign") or "").upper()
        expected = _sign(payload, settings.ltzf_key)
        verified = bool(received_sign) and received_sign == expected
        amount = Decimal(str(payload.get("total_fee") or payload.get("amount") or "0"))
        status_value = str(payload.get("trade_status") or payload.get("status") or "").lower()
        success = status_value in {"success", "trade_success", "1", "paid", "completed"}
        return ParsedNotify(
            provider=f"ltzf_{self.channel}",
            order_no=str(payload.get("out_trade_no") or ""),
            amount=amount,
            trade_no=str(payload.get("transaction_id") or payload.get("trade_no") or ""),
            verified=verified,
            success=success,
            raw={"status": status_value},
        )

    def success_body(self) -> str:
        return "SUCCESS"
