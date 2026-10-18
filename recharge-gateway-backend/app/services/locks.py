"""Redis distributed locks with a graceful single-process fallback."""

from __future__ import annotations

import logging
import secrets
import threading
from contextlib import contextmanager
from typing import Iterator

from app.config import settings

logger = logging.getLogger(__name__)

_client = None
_checked = False
_check_lock = threading.Lock()

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _get_client():
    global _client, _checked
    if _checked:
        return _client
    with _check_lock:
        if _checked:
            return _client
        _checked = True
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
            )
            client.ping()
            _client = client
            logger.info("Distributed locks using Redis")
        except Exception:
            logger.warning("Redis unavailable; distributed lock falls back to database row locks")
            _client = None
    return _client


class LockUnavailable(Exception):
    pass


@contextmanager
def redis_lock(key: str, ttl_seconds: int | None = None) -> Iterator[bool]:
    """Yield True when the lock was acquired, False when another owner holds it.

    When Redis is not configured/reachable the context yields True because the
    database row lock remains the source of truth.
    """
    ttl = ttl_seconds or settings.redeem_lock_ttl_seconds
    client = _get_client()
    if client is None:
        yield True
        return
    token = secrets.token_hex(16)
    acquired = bool(client.set(key, token, nx=True, ex=ttl))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                client.eval(_RELEASE_SCRIPT, 1, key, token)
            except Exception:
                logger.warning("Failed to release Redis lock %s", key)
