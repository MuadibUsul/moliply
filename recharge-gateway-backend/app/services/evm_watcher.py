"""Compatibility shim; the module now lives in :mod:`app.services.chains.evm_watcher`."""

from __future__ import annotations

from app.services.chains.evm_watcher import *  # noqa: F401,F403
from app.services.chains.evm_watcher import (  # noqa: F401
    _amount,
    _config,
    _confirmations,
    _current_block,
    _request,
)
