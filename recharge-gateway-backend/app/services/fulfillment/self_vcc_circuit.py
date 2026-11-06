"""Automatic circuit breaker for the self-owned VCC channel.

Playwright against an external checkout page fails in ways that have nothing to
do with the card: the DOM changes, Cloudflare raises a wall, the proxy stalls.
Each attempt costs roughly a minute of customer waiting before the task falls
back to upstream, so a broken page means every order pays that tax.

Three environment failures inside fifteen minutes open the circuit for thirty
minutes: hybrid tasks then skip the browser entirely and go straight to
upstream. Cooldowns are deliberately short — the circuit is a relief valve, not
a kill switch, and an operator can reset it from the channel page.

Redis carries the state so API and worker share one view. Without Redis it
degrades to a process-local window, which is still correct for the single
process that runs the check and only loses cross-process sharing.
"""

from __future__ import annotations

import logging
import threading
import time

from app.services import feishu
from app.services.locks import redis_client

logger = logging.getLogger(__name__)

FAILURES_KEY = "circuit:self_vcc:failures"
OPEN_KEY = "circuit:self_vcc:open"
WINDOW_SECONDS = 15 * 60
OPEN_SECONDS = 30 * 60
THRESHOLD = 3

_lock = threading.Lock()
_local_failures: list[float] = []
_local_open_until = 0.0


def _now() -> float:
    return time.monotonic()


def _local_open() -> bool:
    global _local_open_until
    with _lock:
        if _local_open_until and _now() >= _local_open_until:
            _local_open_until = 0.0
            _local_failures.clear()
            return False
        return bool(_local_open_until)


def is_open() -> bool:
    """True while the channel is in its forced-upstream window."""
    client = redis_client()
    if client is None:
        return _local_open()
    try:
        return bool(client.get(OPEN_KEY))
    except Exception:  # noqa: BLE001 - a broken breaker must not stop routing
        logger.debug("Circuit state unreadable; treating as closed")
        return _local_open()


def record_failure(code: str) -> int:
    """Count one environment failure; opens the circuit at the threshold."""
    client = redis_client()
    if client is None:
        return _record_local(code)
    try:
        count = int(client.incr(FAILURES_KEY))
        if count == 1:
            client.expire(FAILURES_KEY, WINDOW_SECONDS)
        if count >= THRESHOLD:
            client.setex(OPEN_KEY, OPEN_SECONDS, code)
            _announce(count, code)
        return count
    except Exception:  # noqa: BLE001
        logger.debug("Circuit counter unavailable; using the local window")
        return _record_local(code)


def _record_local(code: str) -> int:
    global _local_open_until
    cutoff = _now() - WINDOW_SECONDS
    with _lock:
        _local_failures[:] = [at for at in _local_failures if at >= cutoff]
        _local_failures.append(_now())
        count = len(_local_failures)
        if count >= THRESHOLD and not _local_open_until:
            _local_open_until = _now() + OPEN_SECONDS
    if count >= THRESHOLD:
        _announce(count, code)
    return count


def _announce(count: int, code: str) -> None:
    try:
        feishu.dispatch_event(
            "channel.fallback",
            "MOLIPLY 自营通道已自动熔断",
            {
                f"连续环境故障": f"{count} 次",
                "最近原因": code,
                "处理": "后续任务直接走上游，30 分钟后自动半开",
            },
            severity="warning",
            link_path="/admin/channels",
        )
    except Exception:  # noqa: BLE001
        logger.debug("Circuit alert could not be sent")


def reset() -> None:
    """Close the circuit and clear the counter, e.g. after a selector fix."""
    global _local_open_until
    with _lock:
        _local_failures.clear()
        _local_open_until = 0.0
    client = redis_client()
    if client is None:
        return
    try:
        client.delete(FAILURES_KEY, OPEN_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("Circuit could not be reset in Redis")


def state() -> dict[str, object]:
    """Operator-facing snapshot for the channel status page."""
    client = redis_client()
    failures = 0
    ttl = 0
    if client is not None:
        try:
            failures = int(client.get(FAILURES_KEY) or 0)
            ttl = int(client.ttl(OPEN_KEY) or 0)
        except Exception:  # noqa: BLE001
            pass
    else:
        with _lock:
            cutoff = _now() - WINDOW_SECONDS
            failures = len([at for at in _local_failures if at >= cutoff])
            ttl = int(max(0.0, _local_open_until - _now()))
    return {
        "open": is_open(),
        "failures_in_window": failures,
        "open_seconds_left": max(0, ttl),
        "threshold": THRESHOLD,
        "window_seconds": WINDOW_SECONDS,
    }


__all__ = ["is_open", "record_failure", "reset", "state"]
