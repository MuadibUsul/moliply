"""Outbound URL validation & SSRF protection for admin-configurable endpoints."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.config import settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def _is_forbidden_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_ssrf_host(hostname: str) -> None:
    """Block literal private/metadata IPs and, in production, DNS resolving to them."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("outbound URL missing hostname")
    if host in _LOCAL_HOSTS:
        if settings.is_production:
            raise ValueError("outbound URL cannot target local hosts in production")
        return
    try:
        ipaddress.ip_address(host)
        if _is_forbidden_ip(host) and settings.is_production:
            raise ValueError("outbound URL cannot target private or metadata IPs")
        return
    except ValueError as exc:
        if "outbound URL" in str(exc):
            raise
    if not settings.is_production:
        return
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        # Let the HTTP client report DNS failures cleanly if resolution is transient.
        return
    for info in infos:
        resolved_ip = info[4][0]
        if _is_forbidden_ip(resolved_ip):
            raise ValueError("outbound URL resolves to a private or metadata IP")


def ensure_https(url: str) -> str:
    """Reject plaintext or SSRF-targeted endpoints, except localhost during dev."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme == "https" and parsed.netloc:
        _check_ssrf_host(parsed.hostname or "")
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
