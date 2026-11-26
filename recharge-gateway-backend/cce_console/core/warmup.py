"""Natural multi-day warm-up engine for free Claude accounts before Pro upgrade.

A brand-new account that registers, binds a credit card within 10 seconds, and
immediately hammers the CLI looks like an automated farm to Anthropic and Stripe
Radar. This module drives low-frequency, human-paced web interactions inside the
profile's persistent Camoufox context across 2-3 days so the account builds
normal session cookies and conversation history before a card is ever attached.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import random
import re
from typing import Any

from core.cloudflare import open_with_challenge_retry

logger = logging.getLogger("cce.warmup")

CHAT_URL = "https://claude.ai/new"

# Varied, benign everyday prompts used during daily warm-up rounds.
WARMUP_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "Could you explain the difference between an index and a primary key in PostgreSQL in three short bullet points?",
        "Thanks! Could you also show a one-line CREATE INDEX example?",
    ),
    (
        "What are three practical tips for writing clean, maintainable Python dataclasses?",
        "How does slots=True affect memory usage on a dataclass?",
    ),
    (
        "Can you summarize the pros and cons of using SQLite in WAL mode for a small local web service?",
        "What PRAGMA busy_timeout value is reasonable for light concurrency?",
    ),
    (
        "Give me a quick comparison between Flexbox and CSS Grid for a two-column dashboard layout.",
        "Which one is simpler for a responsive card grid?",
    ),
    (
        "What is the cleanest way to debounce an input event in vanilla JavaScript without external libraries?",
        "Can you show a 10-line implementation?",
    ),
)

COMPOSER_SELECTORS: tuple[str, ...] = (
    'div[contenteditable="true"].ProseMirror',
    'div[contenteditable="true"]',
    'textarea[placeholder*="Reply"]',
    'textarea[placeholder*="Message"]',
    "textarea",
)

SEND_LABELS: tuple[str, ...] = (
    r"^send message$",
    r"^send$",
    r"^submit$",
)


def default_warmup_days() -> int:
    raw = (os.environ.get("CCE_WARMUP_DAYS") or "3").strip()
    try:
        return max(0, min(14, int(raw)))
    except ValueError:
        return 3


def default_interval_hours() -> float:
    raw = (os.environ.get("CCE_WARMUP_INTERVAL_HOURS") or "20").strip()
    try:
        return max(0.05, min(72.0, float(raw)))
    except ValueError:
        return 20.0


def strict_isp_proxy_enabled() -> bool:
    raw = (os.environ.get("CCE_STRICT_ISP_PROXY") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


_HOT_STANDBY_OVERRIDE: int | None = None


def hot_standby_target() -> int:
    """How many pre-upgraded Pro accounts to keep on the hot-standby shelf.

    - ``0`` (default): **Zero time-value loss**. All accounts rest as $0 mature
      free accounts after their 2-3 day warm-up and only upgrade to Pro when a
      paid order triggers promotion (or the operator clicks fast-track).
    - ``1..N``: Keep ``N`` ready Pro accounts on the shelf for 0-second instant
      delivery; whenever one sells, 1 mature free account automatically promotes
      from the pool to refill the shelf to ``N``.
    """
    if _HOT_STANDBY_OVERRIDE is not None:
        return max(0, min(50, int(_HOT_STANDBY_OVERRIDE)))
    raw = (os.environ.get("CCE_HOT_STANDBY_TARGET") or "0").strip()
    try:
        return max(0, min(50, int(raw)))
    except ValueError:
        return 0


def set_hot_standby_target(target: int) -> int:
    global _HOT_STANDBY_OVERRIDE
    _HOT_STANDBY_OVERRIDE = max(0, min(50, int(target)))
    return _HOT_STANDBY_OVERRIDE


def next_warmup_timestamp(*, base_hours: float | None = None, now: datetime | None = None) -> str:
    """Compute the next warm-up timestamp with ±15% human jitter."""
    ref = now or datetime.now(timezone.utc)
    hours = default_interval_hours() if base_hours is None else max(0.05, float(base_hours))
    jitter_factor = random.uniform(0.85, 1.15)
    target = ref + timedelta(hours=hours * jitter_factor)
    return target.replace(microsecond=0).isoformat()


async def _find_composer(page: Any) -> Any | None:
    for selector in COMPOSER_SELECTORS:
        try:
            loc = page.locator(selector)
            if await loc.count():
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


async def _send_prompt(page: Any, text: str) -> bool:
    composer = await _find_composer(page)
    if composer is None:
        return False
    try:
        await composer.click(timeout=5000)
        await asyncio.sleep(random.uniform(0.4, 0.9))
        await composer.fill(text)
        await asyncio.sleep(random.uniform(0.6, 1.2))
        # Try explicit send button first, then Enter key.
        for pattern in SEND_LABELS:
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            try:
                if await btn.count():
                    await btn.first.click(timeout=4000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        await page.keyboard.press("Enter")
        return True
    except Exception:  # noqa: BLE001
        logger.debug("Warm-up prompt send skipped", exc_info=True)
        return False


async def run_warmup_session(page: Any, *, round_index: int = 1, timeout: float = 120.0) -> dict[str, Any]:
    """Perform one natural free-tier conversation session to build account weight.

    Never raises on minor UI differences: even if the chat composer selector has
    shifted, opening the authenticated home page, dwelling naturally, and
    refreshing session cookies still ages the profile cleanly.
    """
    try:
        await open_with_challenge_retry(page, CHAT_URL, timeout_ms=int(timeout * 1000))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"打不开对话页：{exc}"}

    await asyncio.sleep(random.uniform(2.5, 4.5))
    url_lower = (getattr(page, "url", "") or "").lower()
    if "login" in url_lower:
        return {"ok": False, "reason": "登录态已失效，需重新登录"}

    pair = WARMUP_PROMPTS[(max(1, round_index) - 1) % len(WARMUP_PROMPTS)]
    sent = 0
    if await _send_prompt(page, pair[0]):
        sent += 1
        await asyncio.sleep(random.uniform(8.0, 14.0))
        if await _send_prompt(page, pair[1]):
            sent += 1
            await asyncio.sleep(random.uniform(6.0, 10.0))
    else:
        # Dwell and scroll gently so session telemetry still looks human.
        await asyncio.sleep(random.uniform(3.0, 5.0))

    return {"ok": True, "round": round_index, "messages_sent": sent}
