"""Deep session pre-check that runs before any money or code is spent.

This is the gate between "customer pasted something" and "the queue takes the
activation code". Everything here is either local or a read-only call, so a
failure can never cost the customer a code, a Playwright run or a card charge.

Two promises the caller relies on:

* a rejected session leaves the code ``UNUSED`` — the wizard can safely let the
  customer retry with another account or come back later;
* the response only ever carries the masked mailbox, never the raw credential.

The verdicts map one-to-one onto a friendly message so the wizard can show a
sentence instead of an error code:

===============  =======================================================
``ALREADY_SUBSCRIBED``        paid plan still inside its window
``SESSION_EXPIRED``           the session itself has lapsed
``SESSION_MALFORMED``         JSON is missing pieces the parser needs
``SESSION_INVALID``           the API rejected the access token
``SESSION_TYPE_MISMATCH``     Claude key for a ChatGPT SKU or vice versa
``CODE_BOUND_TO_OTHER_ACCOUNT`` bound to a different mailbox
``ACCOUNT_CHECK_UNAVAILABLE`` remote check down and fail-closed
===============  =======================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.models import ActivationCode, SkuType
from app.services import session_parser
from app.services.errors import PrecheckError
from app.services.session_parser import SessionInfo

logger = logging.getLogger(__name__)

CLAUDE_SKUS = {SkuType.CLAUDE_PRO_1M}
GEMINI_SKUS = {
    SkuType.GEMINI_ADVANCED_1M,
    SkuType.GEMINI_ADVANCED_3M,
    SkuType.GEMINI_ADVANCED_12M,
}
# These redeem without an account, so the pre-check reports success without one.
NO_SESSION_SKUS = {SkuType.CODEX_SMS_US, SkuType.SMS_VERIFY, SkuType.GENERIC_DIGITAL}

Verdict = str


@dataclass
class PrecheckResult:
    verdict: str
    message: str
    account_email: str = ""
    current_plan: str = "unknown"
    warning: str | None = None
    plan_expires_at: str | None = None
    requires_session: bool = True
    session: SessionInfo | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.verdict == "OK"


def _expected_kind(sku_type: SkuType) -> str:
    if sku_type in CLAUDE_SKUS:
        return "claude"
    if sku_type in GEMINI_SKUS:
        return "gemini"
    return "chatgpt"


def _reject(verdict: str, message: str, **kwargs) -> PrecheckResult:
    return PrecheckResult(verdict=verdict, message=message, **kwargs)


def precheck_session(code: ActivationCode, session_raw: str) -> PrecheckResult:
    """Validate credential format, liveness and subscription state.

    Read-only by design: no database write, no lock, no network charge. The
    subscription verdict comes from :func:`session_parser.check_account`, which
    stays local unless the optional OpenAI lookup is switched on.
    """
    requires_session = code.sku_type not in NO_SESSION_SKUS
    if not requires_session:
        return PrecheckResult(
            verdict="OK",
            message="本商品无需账号凭证，核销后直接发放权益",
            requires_session=False,
            current_plan="n/a",
        )

    try:
        session = session_parser.parse_session(session_raw)
    except session_parser.SessionError as exc:
        return _reject(exc.code, exc.message, requires_session=True)

    expected = _expected_kind(code.sku_type)
    if session.kind != expected:
        return _reject(
            "SESSION_TYPE_MISMATCH",
            "凭证类型与商品不匹配：ChatGPT 商品请粘贴 Session JSON，Claude 商品请粘贴 Session Key，Gemini 商品请填写您的 Gmail 邮箱",
            account_email=session.masked_email,
            session=session,
        )

    if code.bound_account_email and code.bound_account_email.lower() != session.email.lower():
        return _reject(
            "CODE_BOUND_TO_OTHER_ACCOUNT",
            "该激活码已绑定其他账号，无法为当前账号充值",
            account_email=session.masked_email,
            session=session,
        )

    check = session_parser.check_account(session)
    expires_iso = (
        session.plan_expires_at.isoformat() + "Z" if session.plan_expires_at else None
    )
    if not check.eligible:
        return _reject(
            check.error_code or "ACCOUNT_NOT_ELIGIBLE",
            check.message,
            account_email=session.masked_email,
            current_plan=check.current_plan,
            session=session,
        )

    return PrecheckResult(
        verdict="OK",
        message=check.message,
        account_email=session.masked_email,
        current_plan=check.current_plan,
        warning=check.warning,
        plan_expires_at=expires_iso,
        session=session,
    )


def as_payload(result: PrecheckResult, expires_at: str | None = None) -> dict:
    """Shape the pre-check for the HTTP layer (already free of credentials)."""
    return {
        "eligible": result.ok,
        "verdict": result.verdict,
        "account_email": result.account_email,
        "current_plan": result.current_plan,
        "message": result.message,
        "warning": result.warning,
        "requires_session": result.requires_session,
        "plan_expires_at": result.plan_expires_at,
        "session_expires_at": expires_at,
        "session_kind": result.session.kind if result.session else "chatgpt",
    }


def status_for(verdict: str) -> int:
    """400 when the credential is broken, 409 when the account cannot take it.

    Parser rejections reuse the parser's own codes (``INVALID_SESSION_TOKEN``,
    ``SESSION_TOO_LARGE``, ...), so the rule is "anything the customer must fix
    by re-copying is a 400".
    """
    if verdict in {
        "SESSION_MALFORMED",
        "SESSION_EXPIRED",
        "SESSION_INVALID",
        "INVALID_SESSION_TOKEN",
        "SESSION_TOO_LARGE",
    }:
        return 400
    return 409


__all__ = ["PrecheckError", "PrecheckResult", "as_payload", "precheck_session", "status_for"]
