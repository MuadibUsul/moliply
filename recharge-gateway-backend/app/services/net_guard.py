"""Outbound URL validation for admin-configurable endpoints."""

from __future__ import annotations

from urllib.parse import urlparse

from app.config import settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def ensure_https(url: str) -> str:
    """Reject plaintext endpoints, except localhost during development."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme == "https" and parsed.netloc:
        return url
    if (
        not settings.is_production
        and parsed.scheme == "http"
        and (parsed.hostname or "").lower() in _LOCAL_HOSTS
    ):
        return url
    raise ValueError("outbound URL must use HTTPS")


def host_allowed(url: str, patterns: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    for pattern in patterns:
        candidate = pattern.strip().lower().rstrip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[2:]
            if host == suffix or host.endswith("." + suffix):
                return True
        elif host == candidate:
            return True
    return False


def ensure_checkout_url(url: str, allowed: list[str]) -> str:
    ensure_https(url)
    if not host_allowed(url, allowed):
        raise ValueError("checkout host is not allowlisted")
    return url
