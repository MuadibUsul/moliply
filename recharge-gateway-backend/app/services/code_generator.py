"""MOLI-XXXX-XXXX-XXXX activation code helpers."""

from __future__ import annotations

import re
import secrets

from app.config import settings

# 0/O/1/I are intentionally excluded to avoid support-ticket typos.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_PATTERN = re.compile(r"^MOLI-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")


def generate_code(prefix: str | None = None) -> str:
    prefix = (prefix or settings.brand_code_prefix or "MOLI").upper()
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)]
    return f"{prefix}-" + "-".join(groups)


def normalize_code(raw: str) -> str:
    """Accept lowercase, spaces, missing separators and return canonical form."""
    if raw is None:
        return ""
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    prefix = (settings.brand_code_prefix or "MOLI").upper()
    if compact.startswith(prefix):
        compact = compact[len(prefix):]
    if compact.startswith("MOLI"):
        compact = compact[4:]
    if len(compact) != 12:
        return raw.strip().upper()
    return f"{prefix}-{compact[0:4]}-{compact[4:8]}-{compact[8:12]}"


def is_valid_code_format(code: str) -> bool:
    return bool(CODE_PATTERN.match(code or ""))
