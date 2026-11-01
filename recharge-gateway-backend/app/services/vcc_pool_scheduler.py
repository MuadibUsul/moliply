"""Compatibility shim; the module now lives in :mod:`app.services.fulfillment.vcc_pool_scheduler`."""

from __future__ import annotations

from app.services.fulfillment.vcc_pool_scheduler import *  # noqa: F401,F403
from app.services.fulfillment.vcc_pool_scheduler import (  # noqa: F401
    HARD_DECLINE_KEYWORDS,
    _alert,
)
