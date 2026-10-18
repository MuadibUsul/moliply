"""V2 regional checkout driver.

Keeps the V1 Playwright implementation as the single browser/3DS engine while
providing the V2 entry point used by the hybrid dispatcher. The card payload
contains the region-specific billing address, timezone and 3DS mailbox.
"""

from __future__ import annotations

from typing import Callable

from app.services.self_vcc_worker import PlaywrightCheckout


def checkout_and_cancel_autorenew(
    checkout_url: str,
    card: dict,
    proxy_url: str,
    on_state: Callable[[str], None] | None = None,
) -> str:
    return PlaywrightCheckout().run(checkout_url, card, proxy_url, on_state=on_state)
