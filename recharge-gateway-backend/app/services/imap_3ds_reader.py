"""Compatibility shim; the module now lives in :mod:`app.services.fulfillment.imap_3ds_reader`."""

from __future__ import annotations

from app.services.fulfillment.imap_3ds_reader import *  # noqa: F401,F403
from app.services.fulfillment.imap_3ds_reader import (  # noqa: F401
    _decode,
    _imap_since,
    _message_text,
)
