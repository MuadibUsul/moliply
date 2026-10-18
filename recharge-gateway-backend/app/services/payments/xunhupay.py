"""Xunhupay (虎皮椒) V3 adapter.

Xunhupay signs the URL-encoded, key-sorted parameter string with the app
secret. As with Lanttu, verify the current field list in the merchant console
before switching ``PAYMENT_MODE`` to ``live``.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError


def _sign(params: dict[str, Any], secret: str) -> str:
    filtered = {
        name: str(value)
        for name, value in params.items()
        if name != "hash" and value is not None and str(value) != ""
    }
    query = urlencode(sorted(filtered.items()))
    return hashlib.md5(f"{query}{secret}".encode("utf-8")).hexdigest().lower()


class XunhupayProvider(PaymentProvider):
    name = "xunhupay"

    def __init__(self) -> None:
        if not (settings.xunhupay_app_id and settings.xunhupay_app_secret):
            raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "虎皮椒支付参数尚未配置")

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        params: dict[str, Any] = {
            "appid": settings.xunhupay_app_id,
            "trade_order_id": order_no,
            "total_fee": f"{amount:.2f}",
            "title": title,
            "time": str(int(time.time())),
            "notify_url": settings.xunhupay_notify,
            "return_url": settings.xunhupay_return,
            "nonce_str": secrets.token_hex(8),
            "type": "WAP",
            "wap_url": settings.public_store_url,
            "wap_name": settings.brand_name_en,
        }
        params["hash"] = _sign(params, settings.xunhupay_app_secret)
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{settings.xunhupay_api_base.rstrip('/')}/payment/do.html",
                    data=params,
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付下单失败，请稍后重试") from exc

        if str(payload.get("errcode")) not in {"0", "200"}:
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", str(payload.get("errmsg") or "支付下单失败"))
        qr_content = str(payload.get("url_qrcode") or payload.get("url") or "")
        if not qr_content:
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付通道未返回二维码内容")
        return PaymentIntent(
            provider="xunhupay",
            qr_content=qr_content,
            pay_url=str(payload.get("url") or ""),
            trade_no=str(payload.get("order_id") or ""),
            expires_in=settings.order_expire_minutes * 60,
            raw={"errcode": payload.get("errcode")},
        )

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        received_hash = str(payload.get("hash") or "").lower()
        expected = _sign(payload, settings.xunhupay_app_secret)
        verified = bool(received_hash) and received_hash == expected
        amount = Decimal(str(payload.get("total_fee") or "0"))
        status_value = str(payload.get("status") or "").lower()
        success = status_value in {"od", "success", "paid", "completed"}
        return ParsedNotify(
            provider="xunhupay",
            order_no=str(payload.get("trade_order_id") or ""),
            amount=amount,
            trade_no=str(payload.get("transaction_id") or payload.get("open_order_id") or ""),
            verified=verified,
            success=success,
            raw={"status": status_value},
        )

    def success_body(self) -> str:
        return "success"
