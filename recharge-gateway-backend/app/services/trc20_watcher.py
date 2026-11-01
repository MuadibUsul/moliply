"""Compatibility shim; the module now lives in :mod:`app.services.chains.trc20_watcher`."""

from __future__ import annotations

from app.services.chains.trc20_watcher import *  # noqa: F401,F403
from app.services.chains.trc20_watcher import (  # noqa: F401
    _config,
    _fetch_transfers,
    _transfer,
)
