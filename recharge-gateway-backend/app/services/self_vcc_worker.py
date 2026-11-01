"""Compatibility shim; the module now lives in :mod:`app.services.fulfillment.self_vcc_worker`."""

from __future__ import annotations

from app.services.fulfillment.self_vcc_worker import *  # noqa: F401,F403
from app.services.fulfillment.self_vcc_worker import (  # noqa: F401
    CHATGPT_SKUS,
    _card_data,
    _create_checkout_url,
)
