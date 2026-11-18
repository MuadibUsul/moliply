"""Locale, timezone and proxy plumbing for an isolated checkout context.

A Stripe charge is only plausible when three facts agree: the card's domicile,
the billing address on the form, and the exit IP. Drifting any one of them is a
classic "card declined" cause, so this module owns the mapping from a billing
region to all three and parses proxy URLs into what Playwright's proxy option
needs (Playwright wants credentials as separate fields, not inline in the URL).

Everything is pure and synchronous, so both the CheckoutContext builder and the
pool scheduler can use it without a browser or a network round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

PH = "PH"
US = "US"

# Chromium rejects socks5 credentials, so SOCKS proxies that carry a user:pass
# must be normalised to socks5h and reported so the operator can adjust.
SUPPORTED_SCHEMES = {"http", "https", "socks5", "socks5h"}

# Anti-automation defaults that keep a fresh context from looking freshly
# installed: a mainstream viewport, a real UA, and an ``en-US`` navigator.
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ContextProfile:
    region: str
    timezone_id: str
    locale: str
    country: str

    @property
    def proxy_required_country(self) -> str:
        return self.country


PROFILES = {
    PH: ContextProfile(region=PH, timezone_id="Asia/Manila", locale="en-PH", country="PH"),
    US: ContextProfile(region=US, timezone_id="America/New_York", locale="en-US", country="US"),
}


def profile_for(region: str) -> ContextProfile:
    return PROFILES.get((region or "").upper(), PROFILES[US])


def profile_for_card(card: dict, region: str) -> ContextProfile:
    """Card-declared overrides win over the region default."""
    base = profile_for(region)
    timezone_id = str(card.get("timezone") or base.timezone_id)
    locale = str(card.get("locale") or base.locale)
    return ContextProfile(
        region=base.region,
        timezone_id=timezone_id,
        locale=locale,
        country=base.country,
    )


@dataclass
class ParsedProxy:
    server: str
    username: str | None = None
    password: str | None = None
    scheme: str = "http"

    def as_playwright(self) -> dict:
        data: dict[str, str] = {"server": self.server}
        if self.username:
            data["username"] = self.username
        if self.password:
            data["password"] = self.password
        return data


def parse_proxy(proxy_url: str | None) -> ParsedProxy | None:
    """Split ``scheme://user:pass@host:port`` into Playwright's proxy dict.

    Returns ``None`` for an empty value so callers can treat "no proxy" and
    "misconfigured proxy" differently. Malformed input raises ``ValueError`` with
    the reason, which the caller turns into a fallback decision.
    """
    raw = (proxy_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported proxy scheme: {scheme or '(none)'}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy url needs host and port")
    host = parsed.hostname
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    server = f"{scheme}://{host}:{parsed.port}"
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if scheme == "socks5" and (username or password):
        # Chromium only accepts SOCKS auth through the socks5h handler.
        server = server.replace("socks5://", "socks5h://", 1)
        scheme = "socks5h"
    return ParsedProxy(server=server, username=username, password=password, scheme=scheme)


def mask_proxy(proxy_url: str | None) -> str:
    """Loggable form: credentials stripped, host and port kept."""
    if not proxy_url:
        return ""
    try:
        parsed = parse_proxy(proxy_url)
    except ValueError:
        return "(invalid)"
    if parsed is None:
        return ""
    return parsed.server


def resolve_card_proxy(card_proxy_url: str | None, fallback: str | None) -> tuple[str | None, str]:
    """Pick the egress for a card: its own sticky proxy, else the region one.

    The second element is the provenance (``card``/``region``/``none``) so the
    task record can show which exit was actually used.
    """
    if card_proxy_url:
        return card_proxy_url, "card"
    if fallback:
        return fallback, "region"
    return None, "none"


__all__ = [
    "ContextProfile",
    "DEFAULT_UA",
    "DEFAULT_VIEWPORT",
    "ParsedProxy",
    "mask_proxy",
    "parse_proxy",
    "profile_for",
    "profile_for_card",
    "resolve_card_proxy",
]
