"""Channel 1: upstream automated recharge API adapter.

The upstream merchant contract differs between suppliers, so the concrete
paths/signature are configurable. The adapter is deliberately strict about
failure classification: a timeout or a temporary upstream outage releases the
activation code so the customer can retry; a completed payment is never
released.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.config import settings
from app.services import runtime_settings as runtime
from app.services.errors import RechargeError

logger = logging.getLogger(__name__)


@dataclass
class UpstreamResult:
    status: str  # SUCCESS | PENDING | FAILED
    order_no: str = ""
    message: str = ""
    error_code: str = ""


class UpstreamProvider:
    def __init__(self) -> None:
        self.mode = runtime.get("upstream_mode", settings.upstream_mode).lower()
        self.base = runtime.get("upstream_api_base", settings.upstream_api_base)
        self.merchant_key = runtime.get("upstream_api_key", settings.upstream_api_key)
        self.secret = runtime.get("upstream_api_secret", settings.upstream_api_secret)
        self.max_wait = runtime.get_int("upstream_max_wait_seconds", settings.upstream_max_wait_seconds)
        self.poll_interval = runtime.get_float("upstream_poll_interval_seconds", settings.upstream_poll_interval_seconds)

    def create_recharge(self, session_raw: str, sku_type: str, upstream_code: str | None) -> UpstreamResult:
        if self.mode == "mock":
            return self._mock_recharge(sku_type)
        return self._http_recharge(session_raw, sku_type, upstream_code)

    # ------------------------------------------------------------------
    # Mock channel (development and demos)
    # ------------------------------------------------------------------
    def _mock_recharge(self, sku_type: str) -> UpstreamResult:
        time.sleep(max(0.0, settings.upstream_mock_delay_seconds))
        if settings.upstream_mock_fail_rate > 0 and random.random() < settings.upstream_mock_fail_rate:
            raise RechargeError(
                "UPSTREAM_TEMPORARY_FAILURE",
                "上游通道暂时繁忙，激活码已自动释放，请稍后重试",
                release_code=True,
            )
        order_no = f"MOCK-UP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
        return UpstreamResult(status="SUCCESS", order_no=order_no, message=f"模拟上游充值成功：{sku_type}")

    # ------------------------------------------------------------------
    # HTTP channel
    # ------------------------------------------------------------------
    def _headers(self, body: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}.{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Merchant-Key": self.merchant_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def _http_recharge(self, session_raw: str, sku_type: str, upstream_code: str | None) -> UpstreamResult:
        if not self.base or not self.merchant_key:
            raise RechargeError("UPSTREAM_NOT_CONFIGURED", "上游充值通道尚未配置，请联系客服")
        base = self.base.rstrip("/")
        payload = {
            "session_raw": session_raw,
            "sku_type": sku_type,
            "upstream_code": upstream_code,
            "merchant": settings.brand_name_en,
            "timestamp": int(time.time()),
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{base}/api/v1/recharge",
                    content=body.encode("utf-8"),
                    headers=self._headers(body),
                )
        except httpx.HTTPError as exc:
            logger.warning("Upstream create request failed: %s", exc.__class__.__name__)
            raise RechargeError(
                "UPSTREAM_TIMEOUT",
                "上游通道响应超时，激活码已自动释放，请稍后重试",
                release_code=True,
            ) from exc
        if response.status_code >= 500:
            raise RechargeError("UPSTREAM_TEMPORARY_FAILURE", "上游通道暂时不可用，请稍后重试")
        if response.status_code >= 400:
            raise RechargeError("UPSTREAM_REJECTED", "上游通道拒绝了本次充值，请联系客服")
        data = response.json()
        order_no = str(data.get("order_no") or data.get("data", {}).get("order_no") or "")
        status_value = str(data.get("status") or data.get("data", {}).get("status") or "").lower()
        message = str(data.get("message") or data.get("msg") or "")
        if not order_no:
            raise RechargeError("UPSTREAM_INVALID_RESPONSE", "上游通道返回数据异常，请联系客服")
        if status_value in {"success", "succeeded", "completed", "done"}:
            return UpstreamResult("SUCCESS", order_no, message)
        if status_value in {"failed", "failure", "declined"}:
            raise RechargeError("UPSTREAM_REJECTED", message or "上游充值失败，请联系客服")
        return self._poll(order_no, message)

    def _poll(self, order_no: str, initial_message: str = "") -> UpstreamResult:
        base = self.base.rstrip("/")
        deadline = time.monotonic() + self.max_wait
        delay = max(1.0, self.poll_interval)
        while time.monotonic() < deadline:
            time.sleep(delay)
            delay = min(delay * 1.5, 30.0)
            try:
                with httpx.Client(timeout=20.0) as client:
                    response = client.get(
                        f"{base}/api/v1/recharge/{order_no}",
                        headers=self._headers(""),
                    )
                if response.status_code >= 400:
                    continue
                data = response.json()
                status_value = str(data.get("status") or data.get("data", {}).get("status") or "").lower()
                message = str(data.get("message") or data.get("msg") or initial_message)
                if status_value in {"success", "succeeded", "completed", "done"}:
                    return UpstreamResult("SUCCESS", order_no, message)
                if status_value in {"failed", "failure", "declined"}:
                    raise RechargeError("UPSTREAM_REJECTED", message or "上游充值失败，请联系客服")
            except RechargeError:
                raise
            except Exception:
                continue
        raise RechargeError(
            "UPSTREAM_TIMEOUT",
            "上游充值确认超时，请稍后在订单中查看或联系客服",
            release_code=False,
        )

    def channel_status(self) -> dict:
        mode = self.mode
        configured = mode == "mock" or bool(self.base and self.merchant_key)
        return {
            "name": "上游 API 通道",
            "mode": mode,
            "status": "online" if configured else "unconfigured",
            "label": "运行正常" if configured else "待配置",
        }
