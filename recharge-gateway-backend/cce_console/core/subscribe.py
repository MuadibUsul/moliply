"""Attach a Pro subscription to an already-logged-in account.

Runs the same profile context the signup used, so the card is entered from the
same IP and fingerprint that created the account.

The card itself comes from ``card_pool``. The pool is deliberately dumb here —
one card at a time, earmarked and released by the caller — because card
concurrency already lives in the MOLIPLY VCC scheduler; duplicating that logic
inside CCE would let the two disagree.

Like signup, every branch returns an outcome. A 3DS challenge is the normal
"needs human" case: the card issuer's page is out of our reach.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from core.cloudflare import open_with_challenge_retry

logger = logging.getLogger(__name__)

PLANS_URL = "https://claude.ai/upgrade"
BILLING_SETTINGS_URL = "https://claude.ai/settings/billing"
CHECKOUT_HOSTS = ("claude.ai", "stripe.com", "checkout.stripe.com", "pay.openai.com")

UPGRADE_LABELS = (
    r"upgrade to pro",
    r"choose pro",
    r"get pro",
    r"upgrade",
    r"subscribe",
)
CHECKOUT_LABELS = (r"subscribe", r"continue", r"checkout", r"pay")

# Card-entry field selectors, most specific first. Stripe's hosted checkout is
# an iframe, so these are tried inside every frame as well as the top document.
CARD_NUMBER_SELECTORS = (
    'input[name="cardnumber"]',
    'input[autocomplete="cc-number"]',
    'input[placeholder*="card number"]',
)
EXPIRY_SELECTORS = (
    'input[name="exp-date"]',
    'input[autocomplete="cc-exp"]',
    'input[placeholder*="MM"]',
)
CVC_SELECTORS = (
    'input[name="cvc"]',
    'input[autocomplete="cc-csc"]',
    'input[placeholder*="CVC"]',
)
ZIP_SELECTORS = (
    'input[name="postal"]',
    'input[autocomplete="postal-code"]',
    'input[name="postalCode"]',
    'input[placeholder*="ZIP"]',
)
NAME_SELECTORS = (
    'input[name="billingName"]',
    'input[autocomplete="cc-name"]',
    'input[name="name"]',
    'input[placeholder*="Full name"]',
    'input[placeholder*="Name on card"]',
)
LINE1_SELECTORS = (
    'input[name="addressLine1"]',
    'input[autocomplete="billing address-line1"]',
    'input[autocomplete="address-line1"]',
)
CITY_SELECTORS = (
    'input[name="locality"]',
    'input[autocomplete="billing address-level2"]',
    'input[autocomplete="address-level2"]',
)

# A 3DS challenge surfaces as one of these.
THREE_DS_MARKERS = ("3ds", "3-d", "sca", "authentication", "verify")


class SubscribeError(RuntimeError):
    pass


@dataclass
class SubscribeOutcome:
    status: str  # subscribed | needs_human | failed
    message: str = ""
    url: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "subscribed"


def _is_three_ds(url: str, body: str = "") -> bool:
    haystack = f"{url} {body}".lower()
    return any(marker in haystack for marker in THREE_DS_MARKERS)


async def _frames(page: Any) -> list[Any]:
    return list(getattr(page, "frames", []) or [])


async def _fill_in_any_frame(page: Any, selectors: tuple[str, ...], value: str) -> bool:
    """Card fields live in a cross-origin iframe; try the page, then each frame."""
    for root in [page, *await _frames(page)]:
        for selector in selectors:
            try:
                locator = root.locator(selector)
                if await locator.count():
                    await locator.first.fill(value)
                    return True
            except Exception:  # noqa: BLE001 - detached frame mid-typing
                continue
    return False


async def _click_in_any_frame(page: Any, labels: tuple[str, ...]) -> bool:
    for root in [page, *await _frames(page)]:
        for label in labels:
            for role in ("button", "link"):
                try:
                    locator = root.get_by_role(role, name=re.compile(label, re.I))
                    if await locator.count():
                        await locator.first.click(timeout=6000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


async def _split_expiry(raw: str) -> tuple[str, str]:
    """Accept MM/YY, MM/YYYY or MMYY and return two parts for Stripe."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) >= 5:  # MMyyyy
        return digits[:2], digits[-2:]
    if len(digits) == 4:  # MMYY
        return digits[:2], digits[2:]
    return digits[:2] or "12", digits[2:] or "30"


async def run_subscribe(
    page: Any,
    *,
    card: dict[str, Any],
    plan: str = "pro",
    timeout: float = 180.0,
    check_interval: float = 4.0,
) -> SubscribeOutcome:
    """Upgrade the logged-in account on this page to a paid plan."""
    try:
        await open_with_challenge_retry(page, PLANS_URL, timeout_ms=int(timeout * 1000))
    except Exception as exc:  # noqa: BLE001
        return SubscribeOutcome("needs_human", f"打不开升级页：{exc}", url=page.url)

    await asyncio.sleep(2.5)
    if "login" in page.url:
        return SubscribeOutcome("needs_human", "会话失效，账号未处于登录态", url=page.url)

    # Choose the plan. The label carries the tier so "Pro" cannot fall through
    # to a different card on the page.
    tier_label = r"(?:choose|get|upgrade to|subscribe to)\s*" + re.escape(plan)
    if not await _click_in_any_frame(page, (tier_label, f"^{re.escape(plan)}$", *UPGRADE_LABELS)):
        return SubscribeOutcome("needs_human", f"找不到升级到 {plan} 的入口", url=page.url)
    await asyncio.sleep(3.5)

    if _is_three_ds(page.url):
        return SubscribeOutcome("needs_human", "进入 3DS 校验，需人工完成", url=page.url)

    await _click_in_any_frame(page, CHECKOUT_LABELS)
    await asyncio.sleep(3.0)

    month, year = await _split_expiry(str(card.get("exp") or ""))
    filled_number = await _fill_in_any_frame(page, CARD_NUMBER_SELECTORS, str(card.get("pan") or ""))
    if not filled_number:
        return SubscribeOutcome("needs_human", "结账页没有找到卡号输入框（可能已是 3DS 或改版）", url=page.url)
    await _fill_in_any_frame(page, EXPIRY_SELECTORS, f"{month}/{year}")
    await _fill_in_any_frame(page, CVC_SELECTORS, str(card.get("cvv") or ""))
    from core import vcc_issuers

    avs = vcc_issuers.billing_profile_for_zip(str(card.get("zip") or "97204"), str(card.get("holder") or ""))
    await _fill_in_any_frame(page, NAME_SELECTORS, avs["holder_name"])
    await _fill_in_any_frame(page, LINE1_SELECTORS, avs["line1"])
    await _fill_in_any_frame(page, CITY_SELECTORS, avs["city"])
    await _fill_in_any_frame(page, ZIP_SELECTORS, avs["zip"])
    await asyncio.sleep(0.8)

    await _click_in_any_frame(page, CHECKOUT_LABELS)
    await asyncio.sleep(4.0)

    # Watch for the three ways this can end: 3DS, success, decline.
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        url = page.url
        if _is_three_ds(url):
            return SubscribeOutcome("needs_human", "发卡行要求 3DS 验证，需人工输入验证码", url=url)
        body = ""
        try:
            body = (await page.inner_text("body"))[:2000]
        except Exception:  # noqa: BLE001
            pass
        lowered = body.lower()
        if any(marker in lowered for marker in ("declined", "card was declined", "insufficient funds")):
            return SubscribeOutcome("failed", f"卡片被拒：{body[:120]}", url=url)
        if any(marker in lowered for marker in ("you're now on pro", "now on the pro plan", "payment successful", "thanks for subscribing")):
            cancelled = False
            if os.environ.get("CCE_CANCEL_AUTORENEW", "true").strip().lower() not in {"0", "false", "no"}:
                cancelled = await _best_effort_cancel_autorenew(page)
            msg = "订阅成功（已关闭自动续费）" if cancelled else "订阅成功"
            return SubscribeOutcome("subscribed", msg, url=url)
        await asyncio.sleep(check_interval)

    return SubscribeOutcome("needs_human", "结账提交后未见明确结果，需人工确认", url=page.url)


CANCEL_PLAN_LABELS = (
    r"cancel plan",
    r"cancel subscription",
    r"cancel auto[- ]?renew",
    r"取消订阅",
)
CONFIRM_CANCEL_LABELS = (
    r"confirm cancellation",
    r"cancel plan",
    r"cancel subscription",
    r"yes,\s*cancel",
    r"确认取消",
)


async def _best_effort_cancel_autorenew(page: Any) -> bool:
    """Turn off month-2 auto-renewal after Pro activates so a reusable VCC is not drained.

    Never raises: if Anthropic changes the billing settings layout, the paid
    subscription is still valid and must proceed to credential extraction.
    """
    try:
        await page.goto(BILLING_SETTINGS_URL, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(2.5)
        if not await _click_in_any_frame(page, CANCEL_PLAN_LABELS):
            return False
        await asyncio.sleep(2.0)
        await _click_in_any_frame(page, CONFIRM_CANCEL_LABELS)
        await asyncio.sleep(2.0)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("Best-effort auto-renew cancellation skipped", exc_info=True)
        return False


__all__ = ["SubscribeError", "SubscribeOutcome", "run_subscribe"]
