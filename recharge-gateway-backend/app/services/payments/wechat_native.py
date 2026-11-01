"""WeChat Pay V3 Native adapter: RSA-SHA256 request signing + AEAD callback.

Native (扫码支付) requires a merchant certificate serial, an RSA private key for
the ``WECHATPAY2-SHA256-RSA2048`` authorization header and the 32-byte APIv3 key
for AEAD_AES_256_GCM callback decryption. Successful decryption already proves
the sender holds the APIv3 key; when a WeChat platform public key is configured
the ``Wechatpay-Signature`` header is verified as well.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from decimal import Decimal
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.services import net_guard
from app.services import runtime_settings as runtime
from app.services.payments.base import ParsedNotify, PaymentIntent, PaymentProvider, PaymentProviderError
from app.services.payments.desensitize import OfficialPaymentGatewayMixin

logger = logging.getLogger(__name__)

NATIVE_PATH = "/v3/pay/transactions/native"

# Reserved keys the callback route injects so the signature can be rebuilt.
RAW_BODY_KEY = "__raw_body"
HEADERS_KEY = "__headers"


def _load_private_key(raw: str):
    return serialization.load_pem_private_key(raw.strip().replace("\\n", "\n").encode("utf-8"), password=None)


def _load_public_key(raw: str):
    return serialization.load_pem_public_key(raw.strip().replace("\\n", "\n").encode("utf-8"))


class WechatNativeProvider(OfficialPaymentGatewayMixin, PaymentProvider):
    name = "wechat_native"

    def __init__(self) -> None:
        self.app_id = runtime.get("wechat_app_id")
        self.mch_id = runtime.get("wechat_mch_id")
        self.mch_serial_no = runtime.get("wechat_mch_serial_no")
        self.api_base = runtime.get("wechat_api_base", settings.wechat_api_base) or settings.wechat_api_base
        self.notify_url = runtime.get("wechat_notify_url", settings.wechat_notify) or settings.wechat_notify
        self.apiv3_key = runtime.get("wechat_apiv3_key")
        private_key = runtime.get("wechat_mch_private_key")
        platform_key = runtime.get("wechat_platform_public_key")
        try:
            net_guard.ensure_https(self.api_base)
        except ValueError as exc:
            raise PaymentProviderError("PAYMENT_INSECURE_URL", "支付 API 必须使用 HTTPS") from exc
        if not (self.app_id and self.mch_id and self.mch_serial_no and private_key and self.apiv3_key):
            raise PaymentProviderError("PAYMENT_NOT_CONFIGURED", "微信支付 V3 参数尚未配置")
        if len(self.apiv3_key.encode("utf-8")) != 32:
            raise PaymentProviderError("PAYMENT_KEY_INVALID", "微信 APIv3 密钥必须是 32 字节")
        try:
            self._private_key = _load_private_key(private_key)
            self._platform_public_key = _load_public_key(platform_key) if platform_key else None
        except Exception as exc:  # noqa: BLE001
            raise PaymentProviderError("PAYMENT_KEY_INVALID", "微信支付密钥格式不正确") from exc

    def _authorization(self, method: str, canonical_url: str, body: str) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{canonical_url}\n{timestamp}\n{nonce}\n{body}\n".encode("utf-8")
        signature = self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mch_id}",'
            f'nonce_str="{nonce}",timestamp="{timestamp}",'
            f'serial_no="{self.mch_serial_no}",'
            f'signature="{base64.b64encode(signature).decode("ascii")}"'
        )

    def create_native(self, order_no: str, amount: Decimal, title: str, client_ip: str = "") -> PaymentIntent:
        payload = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": self.build_safe_subject(order_no),
            "out_trade_no": order_no,
            "notify_url": self.notify_url,
            "amount": {"total": self.to_cents(amount), "currency": "CNY"},
        }
        if client_ip:
            payload["scene_info"] = {"payer_client_ip": client_ip}
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = {
            "Authorization": self._authorization("POST", NATIVE_PATH, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(f"{self.api_base.rstrip('/')}{NATIVE_PATH}", content=body.encode("utf-8"), headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", "支付下单失败，请稍后重试") from exc

        if not data.get("code_url"):
            logger.warning("WeChat Native rejected order: %s", data.get("message"))
            raise PaymentProviderError("PAYMENT_CREATE_FAILED", str(data.get("message") or "支付下单失败"))
        return PaymentIntent(
            provider="wechat_native",
            qr_content=str(data["code_url"]),
            trade_no=order_no,
            expires_in=runtime.get_int("order_expire_minutes", settings.order_expire_minutes) * 60,
            raw={"code_url": data["code_url"]},
        )

    def _signature_valid(self, headers: dict[str, str], raw_body: bytes) -> bool:
        if self._platform_public_key is None:
            # No platform key configured: the AEAD tag is the only integrity proof.
            return True
        timestamp = headers.get("wechatpay-timestamp", "")
        nonce = headers.get("wechatpay-nonce", "")
        signature = headers.get("wechatpay-signature", "")
        if not (timestamp and nonce and signature):
            return False
        message = f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8')}\n".encode("utf-8")
        try:
            self._platform_public_key.verify(
                base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256()
            )
            return True
        except (InvalidSignature, ValueError):
            return False

    def _decrypt(self, resource: dict[str, Any]) -> dict[str, Any]:
        try:
            plaintext = AESGCM(self.apiv3_key.encode("utf-8")).decrypt(
                str(resource["nonce"]).encode("utf-8"),
                base64.b64decode(resource["ciphertext"]),
                str(resource.get("associated_data") or "").encode("utf-8"),
            )
            return json.loads(plaintext.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise PaymentProviderError("PAYMENT_DECRYPT_FAILED", "微信回调报文解密失败") from exc

    def parse_notify(self, payload: dict[str, Any], signature: str | None = None) -> ParsedNotify:
        raw_body = str(payload.get(RAW_BODY_KEY) or "").encode("utf-8")
        headers = {key.lower(): value for key, value in dict(payload.get(HEADERS_KEY) or {}).items()}
        signature_ok = self._signature_valid(headers, raw_body) if raw_body else False

        resource = payload.get("resource")
        if not isinstance(resource, dict):
            raise PaymentProviderError("PAYMENT_DECRYPT_FAILED", "微信回调缺少 resource")
        decrypted = self._decrypt(resource)

        mchid_matches = str(decrypted.get("mchid") or "") == str(self.mch_id)
        trade_state = str(decrypted.get("trade_state") or "").upper()
        total_cents = int((decrypted.get("amount") or {}).get("total") or 0)
        return ParsedNotify(
            provider="wechat_native",
            order_no=str(decrypted.get("out_trade_no") or ""),
            amount=(Decimal(total_cents) / Decimal("100")).quantize(Decimal("0.01")),
            trade_no=str(decrypted.get("transaction_id") or ""),
            verified=signature_ok and mchid_matches,
            success=trade_state == "SUCCESS",
            currency="CNY",
            raw={"status": trade_state, "mchid_matches": mchid_matches},
        )

    def success_body(self) -> str:
        return '{"code":"SUCCESS","message":"成功"}'
