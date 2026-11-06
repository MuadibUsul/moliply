"""Drive Claude's signup flow to a logged-in, credential-bearing account.

This runs inside the profile's own Camoufox context, so the account is created
from that profile's residential IP and pinned fingerprint — the identity is
decided before the account exists, not after.

The flow is best-effort by design: Cloudflare, hCaptcha and "check your inbox"
pages are external and change. Every step returns a *reason* instead of raising
into the caller, and anything a human must judge ends in
``SignupOutcome(status="needs_human")`` with the browser left where it stopped,
so an operator can finish it by hand instead of the run being lost.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.mail_reader import GmailReader, MailError

logger = logging.getLogger(__name__)

# There is no /signup route: signup starts from the marketing page's
# "Continue with email", which lands on login and creates on demand.
SIGNUP_URL = "https://claude.ai"

# Buttons/links that mean "this account does not exist yet" or gate progress.
EMAIL_ENTRY_LABELS = ("continue with email", "sign up with email", "use email", "email")
CONTINUE_LABELS = ("continue", "next", "submit")
SUBMIT_LABELS = ("create account", "sign up", "continue", "verify")

# Where the flow has stopped somewhere only a person can decide.
HUMAN_MARKERS = (
    "phone",
    "sms",
    "verify your phone",
    "captcha",
    "challenge",
    "two-factor",
    "confirm your identity",
)

CAPTCHA_MAX_ATTEMPTS = 3
CODE_TIMEOUT_SECONDS = 240


@dataclass
class SignupOutcome:
    status: str  # created | needs_human | retry | failed
    message: str = ""
    email: str = ""
    password: str = ""
    url: str = ""
    # "retry" means it is worth another run without a human (Cloudflare, a slow
    # mailbox, a transient page) and the caller may just queue it again.
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "created"


class SignupError(RuntimeError):
    pass


def needs_human(url: str) -> bool:
    haystack = url.lower().replace(" ", "-")
    return any(marker.replace(" ", "-") in haystack for marker in HUMAN_MARKERS)


async def _click_first(page: Any, labels: tuple[str, ...], *, roles: tuple[str, ...] = ("button", "link")) -> bool:
    for label in labels:
        for role in roles:
            locator = page.get_by_role(role, name=re.compile(label, re.I))
            try:
                if await locator.count():
                    await locator.first.click(timeout=6000)
                    return True
            except Exception:  # noqa: BLE001 - a mid-attach node is not fatal
                continue
    return False


async def _fill_first(page: Any, selectors: tuple[str, ...], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if await locator.count():
                await locator.first.fill(value)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _solve_captcha(page: Any, attempts: int = CAPTCHA_MAX_ATTEMPTS) -> bool:
    """Click an hCaptcha checkbox if one is present, anywhere in the frame tree.

    Reuses the frame-walking approach from login.py: hCaptcha serves several
    identically-named iframes, so index order is the only stable handle.
    """
    from core import login

    for _ in range(attempts):
        try:
            if await login.solve_captcha(page):
                # A click is not a token; give the widget a moment to settle.
                await asyncio.sleep(3.5)
                if await login.captcha_token(page) != 0:
                    return True
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.5)
    return False


async def _await_code(
    page: Any,
    reader: GmailReader,
    since: datetime,
    *,
    timeout: float = CODE_TIMEOUT_SECONDS,
) -> str | None:
    """Poll the mailbox while the signup page waits for the code."""
    found = await asyncio.to_thread(reader.wait_for_code, since, int(timeout), 4.0)
    return found.code if found else None


async def run_signup(
    page: Any,
    *,
    email: str,
    password: str,
    gmail_app_password: str,
    full_name: str = "",
    timeout: float = 180.0,
) -> SignupOutcome:
    """Create one account. Returns an outcome; never raises for UI drift."""
    reader = GmailReader(email, gmail_app_password)
    started = datetime.utcnow()

    try:
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=int(timeout * 1000))
    except Exception as exc:  # noqa: BLE001
        return SignupOutcome("failed", f"打不开注册页：{exc.__class__.__name__}")

    await asyncio.sleep(3.0)

    # Cloudflare challenge: it usually clears itself, and the marker text is the
    # only signal we get. Wait it out before touching the form.
    for _ in range(6):
        body = ""
        try:
            body = (await page.inner_text("body"))[:400].lower()
        except Exception:  # noqa: BLE001
            pass
        if "security verification" not in body and "verif" not in page.url.lower():
            break
        await _solve_captcha(page)
        await asyncio.sleep(4.0)

    # Step 1: offer the email path, then enter the address.
    for _ in range(3):
        if await _click_first(page, EMAIL_ENTRY_LABELS):
            break
        await asyncio.sleep(2.5)
    await asyncio.sleep(1.5)
    if not await _fill_first(
        page,
        ('input[type="email"]', 'input[name="email"]', 'input[autocomplete="email"]', "#email"),
        email,
    ):
        # Usually a Cloudflare interstitial still up, or a half-rendered page:
        # both clear on a fresh run.
        return SignupOutcome(
            "needs_human", "找不到邮箱输入框（可能仍被拦截），可重跑", url=page.url, retryable=True
        )
    await _click_first(page, CONTINUE_LABELS)
    await asyncio.sleep(2.0)

    if needs_human(page.url):
        if not await _solve_captcha(page):
            return SignupOutcome("needs_human", "提交邮箱后出现验证码，可重跑", url=page.url, retryable=True)

    # Step 2: the mailbox code. Anything else means the address is taken or the
    # flow diverged.
    try:
        code = await _await_code(page, reader, started)
    except MailError as exc:
        # A wrong app password never fixes itself; a slow mail server might.
        fatal = "登录失败" in str(exc)
        return SignupOutcome(
            "needs_human", f"收不到验证码：{exc}", url=page.url, retryable=not fatal
        )
    if code is None:
        return SignupOutcome(
            "needs_human", "等待 Gmail 验证码超时，可重跑", url=page.url, retryable=True
        )

    if not await _fill_first(
        page,
        (
            'input[autocomplete="one-time-code"]',
            'input[name="code"]',
            'input[inputmode="numeric"]',
            'input[type="text"]',
        ),
        code,
    ):
        return SignupOutcome("needs_human", f"填入验证码 {code} 失败，可重跑", url=page.url, retryable=True)
    await _click_first(page, SUBMIT_LABELS)
    await asyncio.sleep(3.0)

    if needs_human(page.url):
        if not await _solve_captcha(page):
            return SignupOutcome("needs_human", f"验证码已填写（{code}），后续出现验证码/手机校验，需人工", url=page.url)

    # Step 3: password, if this build still asks for one.
    password_set = await _fill_first(
        page,
        ('input[type="password"]', 'input[name="password"]', 'input[autocomplete="new-password"]'),
        password,
    )
    if password_set:
        await _click_first(page, SUBMIT_LABELS)
        await asyncio.sleep(3.0)

    # Step 4: name, when prompted.
    if full_name:
        await _fill_first(
            page,
            ('input[name="name"]', 'input[autocomplete="name"]', 'input[placeholder*="ame"]'),
            full_name,
        )
        await _click_first(page, CONTINUE_LABELS)
        await asyncio.sleep(2.0)

    await asyncio.sleep(2.0)
    if needs_human(page.url):
        return SignupOutcome("needs_human", "注册流程停在人机校验环节，可重跑", url=page.url, retryable=True)

    # A logged-in shell means the account exists and holds a session.
    signed_in = bool(
        await page.locator('[data-testid="user-menu-button"], button[aria-label*="account"], nav').count()
    ) or "signup" not in page.url
    if not signed_in:
        return SignupOutcome("needs_human", "未能确认注册完成，需人工确认", url=page.url)

    return SignupOutcome(
        "created",
        "账号已注册并处于登录态",
        email=email,
        password=password,
        url=page.url,
    )


__all__ = ["SignupError", "SignupOutcome", "run_signup", "MailError"]
