"""Cloudflare interstitial handling.

claude.ai puts a "Just a moment..." challenge in front of the app for most
datacenter exits. In practice the first visit under a fresh profile lands on it
and a reload resolves it: the challenge sets `__cf_bm`, and the second request
carries that cookie plus a warmed fingerprint. Waiting on the first page alone
never clears it, which is what made every proxy look dead.

The helper therefore retries the navigation a bounded number of times and only
gives up when reloads also stay on the wall.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "just a moment",
    "performing security verification",
    "verify you are human",
    "checking your browser",
)
TITLE_LIMIT = 120
# Reloads that still land on the wall before the exit is declared unusable.
MAX_ROUNDS = 3
# Time we allow one page to solve itself before reloading.
SETTLE_SECONDS = 4.0


def looks_blocked(title: str, body: str = "") -> bool:
    haystack = f"{title or ''} {body or ''}".lower()[:2000]
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


async def open_with_challenge_retry(
    page: Any,
    url: str,
    *,
    rounds: int = MAX_ROUNDS,
    settle_seconds: float = SETTLE_SECONDS,
    timeout_ms: int = 90_000,
) -> Any:
    """Navigate until the interstitial is gone, returning the final response.

    Raises ``BrowserPoolError``-compatible ``RuntimeError`` when every round is
    still walled, so the caller parks the profile instead of driving a login form
    that is not actually on screen.
    """
    last_title = ""
    for round_no in range(1, rounds + 1):
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        waited = 0.0
        while waited < settle_seconds * 5:
            await asyncio.sleep(settle_seconds)
            waited += settle_seconds
            try:
                title = await page.title()
            except Exception:  # noqa: BLE001 - navigation may be mid-flight
                title = ""
            if not looks_blocked(title):
                if round_no > 1:
                    logger.info("Cloudflare cleared on round %s for %s", round_no, page.url)
                return response
            last_title = title
        # A walled page stays walled; a reload with __cf_bm set is what works.
        logger.info("Cloudflare still challenging (round %s/%s), reloading", round_no, rounds)

    raise RuntimeError(f"cloudflare challenge did not clear: {last_title[:TITLE_LIMIT]!r}")


__all__ = ["looks_blocked", "open_with_challenge_retry", "MAX_ROUNDS"]
