"""AES-256-GCM helpers for sensitive values.

Card numbers, CVVs, upstream card keys, delivered account credentials and the
short-lived ChatGPT access token are never stored in plaintext. The encryption
key is supplied through ``MASTER_AES_KEY`` (base64, 32 bytes). In development a
deterministic fallback is derived from ``SECRET_KEY`` so the demo still runs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)
_warned_fallback = False


@lru_cache(maxsize=1)
def _encryption_key() -> bytes:
    global _warned_fallback
    raw = settings.master_aes_key.strip()
    if raw:
        try:
            key = base64.b64decode(raw)
        except Exception as exc:  # pragma: no cover - configuration error
            raise RuntimeError("MASTER_AES_KEY must be valid base64") from exc
        if len(key) != 32:
            raise RuntimeError("MASTER_AES_KEY must decode to exactly 32 bytes (AES-256)")
        return key

    if settings.is_production:
        raise RuntimeError("MASTER_AES_KEY is required when APP_ENV=production")

    if not _warned_fallback:
        logger.warning("MASTER_AES_KEY is not set; deriving a development key from SECRET_KEY")
        _warned_fallback = True
    return hashlib.sha256(settings.secret_key.encode("utf-8")).digest()


def encrypt_text(plaintext: str) -> str:
    if plaintext is None:
        raise ValueError("plaintext is required")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_encryption_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_text(token: str) -> str:
    if not token:
        raise ValueError("ciphertext is required")
    blob = base64.b64decode(token)
    if len(blob) < 13:
        raise ValueError("ciphertext is malformed")
    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(_encryption_key()).decrypt(nonce, ciphertext, None).decode("utf-8")


def encrypt_json(value: Any) -> str:
    return encrypt_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def decrypt_json(token: str) -> Any:
    return json.loads(decrypt_text(token))


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if not local:
        return "***@" + domain
    if len(local) == 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_card_number(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 8:
        return "****"
    return f"{digits[:4]} **** **** {digits[-4:]}"


def luhn_valid(number: str) -> bool:
    digits = [int(ch) for ch in number if ch.isdigit()]
    if len(digits) < 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0
