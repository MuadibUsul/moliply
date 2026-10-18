"""Parse and pre-check the session material pasted by a customer.

Two session formats are supported:

* ChatGPT: the JSON returned by ``https://chatgpt.com/api/auth/session``.
* Claude: the ``sessionKey`` cookie value (or a JSON object carrying it)
  used by the upstream Claude recharge channel.

The parser performs a zero-cost local validation first. When
``OPENAI_ACCOUNT_CHECK_ENABLED`` is on it also asks OpenAI whether the account
already has an active Plus/Pro plan, so the activation code is never locked for
an account that does not need a recharge.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.crypto import mask_email

logger = logging.getLogger(__name__)

ACTIVE_PLAN_TOKENS = {"plus", "pro", "team", "enterprise", "business", "chatgptplusplan", "chatgptpro"}
PLAN_LABELS = {
    "free": "free",
    "basic": "free",
    "plus": "plus",
    "chatgptplusplan": "plus",
    "pro": "pro",
    "chatgptpro": "pro",
    "team": "team",
    "enterprise": "enterprise",
}


class SessionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class SessionInfo:
    kind: str
    email: str
    user_id: str
    access_token: str
    raw: str
    expires_at: datetime
    plan: str = "unknown"
    display_name: str = ""
    currency_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def masked_email(self) -> str:
        return mask_email(self.email)

    @property
    def plan_label(self) -> str:
        return PLAN_LABELS.get(self.plan.lower(), self.plan or "unknown")


@dataclass
class AccountCheck:
    eligible: bool
    current_plan: str
    message: str
    error_code: str | None = None
    warning: str | None = None


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _jwt_exp(token: str) -> datetime | None:
    """Read the ``exp`` claim without verifying the signature (local expiry only)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(float(exp), tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None
    return None


def _extract_claude_token(raw: str, data: dict[str, Any] | None) -> str | None:
    if data:
        for key in ("sessionKey", "session_key", "sessionToken", "session_token", "token", "accessToken"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        cookie = data.get("cookie") or data.get("cookies")
        if isinstance(cookie, str):
            match = re.search(r"sessionKey=([^;\s]+)", cookie)
            if match:
                return match.group(1)
    match = re.search(r"sessionKey=([^;\s\"']+)", raw)
    if match:
        return match.group(1).strip()
    token_match = re.search(r"(sk-ant-[A-Za-z0-9_\-\.]+)", raw)
    if token_match:
        return token_match.group(1)
    stripped = raw.strip()
    if stripped.startswith("sk-ant-") and len(stripped) > 20:
        return stripped
    return None


def parse_session(raw: str) -> SessionInfo:
    if not raw or not raw.strip():
        raise SessionError("INVALID_SESSION_TOKEN", "请粘贴账户 Session 信息后再继续")
    if len(raw.encode("utf-8")) > settings.max_session_bytes:
        raise SessionError("SESSION_TOO_LARGE", "提交的 Session 内容过大，请重新复制")

    text = raw.strip()
    data: dict[str, Any] | None = None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionError("INVALID_SESSION_TOKEN", "Session JSON 格式不正确，请完整复制后重试") from exc
        if not isinstance(parsed, dict):
            raise SessionError("INVALID_SESSION_TOKEN", "Session JSON 结构不正确")
        data = parsed
        if isinstance(data.get("session"), dict) and data["session"].get("accessToken"):
            data = data["session"]

    # Claude session keys are long opaque strings; support them before the
    # ChatGPT-specific validation below.
    looks_claude = bool(
        (data and any(key in data for key in ("sessionKey", "session_key", "claude")))
        or re.search(r"sessionKey=", text)
        or text.startswith("sk-ant-")
    )
    if looks_claude:
        token = _extract_claude_token(text, data)
        if not token:
            raise SessionError("INVALID_SESSION_TOKEN", "未找到 Claude Session Key，请重新复制")
        email = ""
        if data:
            email = str(data.get("email") or (data.get("account") or {}).get("email") or "")
        if not email:
            # The upstream channel resolves the real account; this deterministic
            # placeholder keeps the non-null audit field satisfied.
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]
            email = f"claude-{digest}@session.moliply.local"
        return SessionInfo(
            kind="claude",
            email=email,
            user_id="claude-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24],
            access_token=token,
            raw=text,
            expires_at=datetime.utcnow() + timedelta(hours=12),
            plan="unknown",
        )

    if data is None:
        raise SessionError(
            "INVALID_SESSION_TOKEN",
            "请粘贴完整的 Session JSON（以 { 开头），或 Claude Session Key",
        )

    access_token = data.get("accessToken") or data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise SessionError("INVALID_SESSION_TOKEN", "Session 中缺少 accessToken，请重新登录后复制")

    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    email = str(user.get("email") or data.get("email") or account.get("email") or "").strip()
    if not email or "@" not in email:
        raise SessionError("INVALID_SESSION_TOKEN", "Session 中缺少账号邮箱，请确认复制的是完整 JSON")

    expires_at = (
        _parse_iso(data.get("expires"))
        or _parse_iso(data.get("expires_at"))
        or _jwt_exp(access_token)
    )
    if expires_at is None:
        raise SessionError("INVALID_SESSION_TOKEN", "Session 中缺少有效期信息，请重新复制")
    if expires_at <= datetime.utcnow():
        raise SessionError("INVALID_SESSION_TOKEN", "Session 已过期，请重新登录 chatgpt.com 后复制")

    user_id = str(user.get("id") or data.get("user_id") or account.get("id") or "")
    plan_raw = str(
        account.get("planType")
        or account.get("plan_type")
        or data.get("planType")
        or data.get("plan")
        or user.get("plan")
        or "free"
    ).lower()
    plan = PLAN_LABELS.get(plan_raw, plan_raw)
    currency_raw = (
        account.get("currency")
        or account.get("billingCurrency")
        or data.get("currency")
        or data.get("billing_currency")
        or user.get("currency")
    )
    currency_hint = str(currency_raw).upper() if currency_raw else None
    return SessionInfo(
        kind="chatgpt",
        email=email,
        user_id=user_id or hashlib.sha256(email.encode("utf-8")).hexdigest()[:24],
        access_token=access_token.strip(),
        raw=text,
        expires_at=expires_at,
        plan=plan,
        display_name=str(user.get("name") or ""),
        currency_hint=currency_hint,
    )


def _scan_plan_payload(payload: Any) -> tuple[bool, str]:
    """Best-effort scan of OpenAI account JSON for an active paid plan."""
    text = json.dumps(payload, ensure_ascii=False).lower()
    active = any(
        marker in text
        for marker in (
            '"is_paid_subscription_active":true',
            '"is_paid_subscription_active": true',
            '"subscription_plan":"chatgptplusplan"',
            '"subscription_plan": "chatgptplusplan"',
            '"subscription_plan":"chatgptpro"',
            '"subscription_plan": "chatgptpro"',
            '"plan_type":"plus"',
            '"plan_type": "plus"',
        )
    )
    plan = "free"
    for candidate in ("chatgptpro", "chatgptplusplan", "plus", "pro", "team"):
        if f'"{candidate}"' in text:
            plan = PLAN_LABELS.get(candidate, candidate)
            break
    return active, plan


def check_account(session: SessionInfo) -> AccountCheck:
    """Zero-cost plan pre-check.

    A locally-declared Plus/Pro plan is rejected immediately. The optional
    remote check catches stale local JSON where the account was upgraded after
    the session was copied.
    """
    if session.kind != "chatgpt":
        return AccountCheck(True, "unknown", "Claude Session 已接收，将由上游通道完成核验与充值")

    if session.plan in ACTIVE_PLAN_TOKENS:
        return AccountCheck(
            False,
            session.plan,
            "该账号当前已是会员，无需重复充值，激活码未锁定",
            error_code="ALREADY_SUBSCRIBED",
        )

    if not settings.openai_account_check_enabled:
        return AccountCheck(True, session.plan, "账号状态正常，可立即通过模力补给升级 Plus")

    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    }
    proxy = settings.openai_proxy_url or None
    try:
        with httpx.Client(timeout=15.0, proxy=proxy) as client:
            response = client.get(
                f"{settings.openai_api_base.rstrip('/')}/backend-api/accounts/check/v4-2023-04-27",
                headers=headers,
            )
        if response.status_code in (401, 403):
            return AccountCheck(
                False,
                session.plan,
                "Session 已失效或无权限，请重新登录后复制",
                error_code="INVALID_SESSION_TOKEN",
            )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI account check returned {response.status_code}")
        active, plan = _scan_plan_payload(response.json())
        if active or plan in ACTIVE_PLAN_TOKENS:
            return AccountCheck(
                False,
                plan,
                "该账号当前已是会员，无需重复充值，激活码未锁定",
                error_code="ALREADY_SUBSCRIBED",
            )
        return AccountCheck(True, plan, "账号状态正常，可立即通过模力补给升级 Plus")
    except Exception as exc:  # noqa: BLE001 - network failures must not log session data
        logger.warning("OpenAI account pre-check failed: %s", exc.__class__.__name__)
        if settings.openai_account_check_fail_open:
            return AccountCheck(
                True,
                session.plan,
                "账号状态正常，可立即通过模力补给升级 Plus",
                warning="账户状态预检暂时不可用，将在支付前再次核验",
            )
        return AccountCheck(
            False,
            session.plan,
            "账户状态预检暂时不可用，请稍后重试或联系客服",
            error_code="ACCOUNT_CHECK_UNAVAILABLE",
        )
