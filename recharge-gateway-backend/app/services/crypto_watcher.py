"""Unified scanner for all self-hosted direct crypto payments."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services import btc_watcher, evm_watcher, trc20_watcher

logger = logging.getLogger(__name__)


def scan_pending_orders(db: Session) -> int:
    matched = 0
    for scanner in (trc20_watcher, evm_watcher, btc_watcher):
        try:
            matched += scanner.scan_pending_orders(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Direct crypto scanner %s failed: %s", scanner.__name__, exc.__class__.__name__)
    return matched
