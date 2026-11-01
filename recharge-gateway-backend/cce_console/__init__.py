"""Vendored Claude cluster console, mounted at /cce inside the admin app.

The vendored tree uses bare top-level packages (``api``, ``core``, ``db``), so
its directory is added to ``sys.path`` on import instead of being rewritten.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)


def create_console_app():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    os.environ.setdefault("CCE_BASE_PATH", "/cce")
    from api.app import create_app

    return create_app()
