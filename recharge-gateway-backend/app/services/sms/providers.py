"""Upstream SMS number providers.

Both drivers answer the same three questions — lease a number, has the OTP
arrived, cancel and get the money back — so the service layer never branches on
supplier. Only the provider's own vocabulary differs, and each driver owns that
translation.

Cancellation is the load-bearing call: it is what makes "no code arrived" free
for the customer *and* for the shop, so every terminal failure path calls it.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)

# 4-8 digits, not part of a longer number and not a currency/amount.
OTP_PATTERN = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
# Numbers like "2026" or "100" show up in receipts; an OTP is 4-8 digits and
# this is only a last-resort filter after the pattern match.
WEAK_CODES = {"0000", "1111", "2024", "2025", "2026", "1234"}


class SmsProviderError(Exception):
    """A supplier-side failure. ``transient`` marks retry-worthy conditions."""

    def __init__(self, code: str, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


class NoNumbersAvailable(SmsProviderError):
    def __init__(self, message: str = "该地区暂无可用号码") -> None:
        super().__init__("NO_NUMBERS", message)


@dataclass
class LeasedNumber:
    upstream_order_id: str
    phone_number: str
    provider: str
    raw: dict = field(default_factory=dict)


@dataclass
class ReceivedSms:
    """One poll result. ``code`` is set only when a plausible OTP was parsed."""

    arrived: bool
    code: str | None = None
    text: str = ""


def extract_otp(text: str) -> str | None:
    """Pull the most likely verification code out of a message body.

    The *last* strong match wins: senders usually lead with "your code is" and
    trail with the code, while leading numbers are often amounts or references.
    """
    if not text:
        return None
    matches = [match.group(1) for match in OTP_PATTERN.finditer(text)]
    if not matches:
        return None
    for candidate in reversed(matches):
        if candidate not in WEAK_CODES:
            return candidate
    return matches[-1]


class BaseSMSProvider(ABC):
    """Contract every upstream must satisfy."""

    name: str = "base"

    @abstractmethod
    def configured(self) -> bool:
        """True when credentials are present; the router skips unconfigured ones."""

    @abstractmethod
    def lease_number(self, service: str, country: str) -> LeasedNumber:
        """Buy one number. Raises :class:`NoNumbersAvailable` when the pool is dry."""

    @abstractmethod
    def poll_code(self, lease: LeasedNumber) -> ReceivedSms:
        """Check once, never block. Returns ``arrived=False`` while waiting."""

    @abstractmethod
    def cancel(self, lease: LeasedNumber) -> bool:
        """Return the number and refund the balance. Idempotent.

        Returns whether the supplier accepted it. A failure here is logged but
        must never break the customer's number change.
        """

    def _client(self, base: str, timeout: float = 20.0) -> httpx.Client:
        return httpx.Client(base_url=base.rstrip("/"), timeout=timeout)


# ---------------------------------------------------------------------------
# SMSPool
# ---------------------------------------------------------------------------


class SMSPoolProvider(BaseSMSProvider):
    """https://api.smspool.net — form-encoded, money per activation."""

    name = "smspool"
    BASE = "https://api.smspool.net"

    def __init__(self) -> None:
        self.api_key = runtime.get("smspool_api_key", "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(self, path: str, data: dict) -> dict:
        payload = {**data, "key": self.api_key}
        try:
            with self._client(self.BASE) as client:
                response = client.post(path, data=payload)
        except httpx.HTTPError as exc:
            raise SmsProviderError("SMS_NETWORK", "接码上游网络异常", transient=True) from exc
        if response.status_code >= 500:
            raise SmsProviderError("SMS_UPSTREAM_DOWN", "接码上游暂时不可用", transient=True)
        if response.status_code >= 400:
            raise SmsProviderError("SMS_UPSTREAM_REJECTED", "接码上游拒绝了请求")
        try:
            body = response.json()
        except ValueError as exc:
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游返回非 JSON") from exc
        if not isinstance(body, dict):
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游返回结构异常")
        if str(body.get("success", 1)) == "0" or body.get("error"):
            message = str(body.get("message") or body.get("error") or "")
            if "out of stock" in message.lower() or "no numbers" in message.lower():
                raise NoNumbersAvailable()
            raise SmsProviderError("SMS_UPSTREAM_REJECTED", message or "接码上游拒绝了请求")
        return body

    def lease_number(self, service: str, country: str) -> LeasedNumber:
        body = self._request(
            "/purchase/sms",
            {"country": country, "service": service, "quantity": 1},
        )
        order_id = body.get("order_id") or body.get("orderid")
        number = body.get("number") or body.get("phonenumber")
        if order_id is None or not number:
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游未返回号码")
        return LeasedNumber(
            upstream_order_id=str(order_id),
            phone_number=str(number),
            provider=self.name,
            raw=body,
        )

    def poll_code(self, lease: LeasedNumber) -> ReceivedSms:
        body = self._request("/sms/check", {"orderid": lease.upstream_order_id})
        status = str(body.get("status", "")).lower()
        text = str(body.get("sms") or body.get("code") or "")
        if status in {"3", "complete", "completed"} and text:
            return ReceivedSms(True, extract_otp(text), text)
        if text and status not in {"1", "pending", "waiting"}:
            # Some suppliers hand the message over before flipping status.
            return ReceivedSms(True, extract_otp(text), text)
        return ReceivedSms(False)

    def cancel(self, lease: LeasedNumber) -> bool:
        try:
            self._request("/sms/cancel", {"orderid": lease.upstream_order_id})
            return True
        except SmsProviderError as exc:
            logger.warning("SMSPool cancel failed for %s: %s", lease.upstream_order_id, exc.code)
            return False


# ---------------------------------------------------------------------------
# 5sim
# ---------------------------------------------------------------------------


class FiveSimProvider(BaseSMSProvider):
    """https://5sim.net/v1 — bearer token, JSON, explicit Non-VoIP operator."""

    name = "fivesim"
    BASE = "https://5sim.net/v1"
    # 5sim exposes `any`; it resolves to a real mobile operator (Non-VoIP)
    # rather than a virtual range, which is what the storefront sells.
    OPERATOR = "any"

    def __init__(self) -> None:
        self.api_key = runtime.get("fivesim_api_key", "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(self, method: str, path: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        try:
            with self._client(self.BASE) as client:
                response = client.request(method, path, headers=headers)
        except httpx.HTTPError as exc:
            raise SmsProviderError("SMS_NETWORK", "接码上游网络异常", transient=True) from exc
        if response.status_code == 404:
            raise NoNumbersAvailable()
        if response.status_code == 402:
            raise SmsProviderError("SMS_LOW_BALANCE", "接码上游余额不足")
        if response.status_code >= 500:
            raise SmsProviderError("SMS_UPSTREAM_DOWN", "接码上游暂时不可用", transient=True)
        if response.status_code >= 400:
            raise SmsProviderError("SMS_UPSTREAM_REJECTED", "接码上游拒绝了请求")
        try:
            body = response.json()
        except ValueError as exc:
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游返回非 JSON") from exc
        if not isinstance(body, dict):
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游返回结构异常")
        if body.get("error"):
            raise SmsProviderError("SMS_UPSTREAM_REJECTED", str(body["error"]))
        return body

    def lease_number(self, service: str, country: str) -> LeasedNumber:
        body = self._request(
            "GET", f"/user/buy/activation/{country.lower()}/{self.OPERATOR}/{service}"
        )
        order_id = body.get("id")
        number = body.get("phone")
        if order_id is None or not number:
            raise SmsProviderError("SMS_BAD_RESPONSE", "接码上游未返回号码")
        return LeasedNumber(
            upstream_order_id=str(order_id),
            phone_number="+" + str(number).lstrip("+"),
            provider=self.name,
            raw=body,
        )

    def poll_code(self, lease: LeasedNumber) -> ReceivedSms:
        body = self._request("GET", f"/user/check/{lease.upstream_order_id}")
        sms = body.get("sms")
        if isinstance(sms, list) and sms:
            # Newest message last; take the last one carrying any text.
            for entry in reversed(sms):
                text = str((entry or {}).get("text") or "").strip()
                if text:
                    return ReceivedSms(True, extract_otp(text), text)
        status = str(body.get("status", "")).lower()
        if status in {"canceled", "cancelled", "expired", "finished", "complete"}:
            return ReceivedSms(False)
        return ReceivedSms(False)

    def cancel(self, lease: LeasedNumber) -> bool:
        try:
            self._request("GET", f"/user/cancel/{lease.upstream_order_id}")
            return True
        except SmsProviderError as exc:
            logger.warning("5sim cancel failed for %s: %s", lease.upstream_order_id, exc.code)
            return False


PROVIDERS: dict[str, type[BaseSMSProvider]] = {
    SMSPoolProvider.name: SMSPoolProvider,
    FiveSimProvider.name: FiveSimProvider,
}


def build_provider(name: str) -> BaseSMSProvider:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise SmsProviderError("SMS_UNKNOWN_PROVIDER", f"未知接码上游：{name}")
    return cls()


def _weight(name: str) -> int:
    return runtime.get_int(f"sms_weight_{name}", 0)


def available_providers() -> list[str]:
    """Configured providers, highest weight first, ties broken by name."""
    names = [name for name, cls in PROVIDERS.items() if cls().configured()]
    return sorted(names, key=lambda name: (-_weight(name), name))


def choose_provider(preferred: str | None = None) -> BaseSMSProvider:
    """Pick an upstream: an explicit preference wins if it is configured."""
    if preferred:
        candidate = build_provider(preferred)
        if candidate.configured():
            return candidate
    names = available_providers()
    if not names:
        raise SmsProviderError("SMS_NOT_CONFIGURED", "接码通道尚未配置，请联系客服")
    return build_provider(names[0])


__all__ = [
    "BaseSMSProvider",
    "FiveSimProvider",
    "LeasedNumber",
    "NoNumbersAvailable",
    "PROVIDERS",
    "ReceivedSms",
    "SMSPoolProvider",
    "SmsProviderError",
    "available_providers",
    "build_provider",
    "choose_provider",
    "extract_otp",
]
