"""Pre-flight proxy verification — the kill switch.

Nothing launches a browser or a CLI session before this passes. It proves the
proxy actually works *and* that traffic egresses from the IP the profile was
created with. A drifting exit IP is the single most common cause of a ban
cluster, so a mismatch aborts instead of "trying anyway".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx

from core.secret_export import export_secrets
from db.database import Database

GEO_ENDPOINT = "http://ip-api.com/json/?fields=status,message,countryCode,regionName,city,lat,lon,timezone,query,isp,as,org,proxy,hosting"

# country -> BCP 47 locale, used for navigator.language / Accept-Language.
# Deliberately a small explicit table: guessing region variants produces
# identities that look wrong to Cloudflare ("en" on a US residential IP).
LOCALE_BY_COUNTRY: dict[str, str] = {
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "AU": "en-AU",
    "NZ": "en-NZ",
    "IE": "en-IE",
    "SG": "en-SG",
    "IN": "en-IN",
    "DE": "de-DE",
    "AT": "de-AT",
    "CH": "de-CH",
    "FR": "fr-FR",
    "BE": "fr-BE",
    "NL": "nl-NL",
    "ES": "es-ES",
    "MX": "es-MX",
    "AR": "es-AR",
    "IT": "it-IT",
    "PT": "pt-PT",
    "BR": "pt-BR",
    "SE": "sv-SE",
    "NO": "nb-NO",
    "DK": "da-DK",
    "FI": "fi-FI",
    "PL": "pl-PL",
    "CZ": "cs-CZ",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "TW": "zh-TW",
    "HK": "zh-HK",
    "CN": "zh-CN",
    "IL": "he-IL",
    "TR": "tr-TR",
    "RU": "ru-RU",
    "UA": "uk-UA",
    "ZA": "en-ZA",
    "AE": "en-AE",
}

FALLBACK_LOCALE = "en-US"


class ProxyKillSwitchError(RuntimeError):
    """Raised when a profile must not start: dead tunnel or drifted egress IP."""

    def __init__(self, message: str, *, profile_id: str | None = None, reason: str = "unknown") -> None:
        super().__init__(message)
        self.profile_id = profile_id
        self.reason = reason


@dataclass(frozen=True)
class ProxyGeoInfo:
    ip: str
    country_code: str
    timezone: str
    latitude: float
    longitude: float
    locale: str
    region: str = ""
    city: str = ""
    isp: str = ""
    asn: str = ""
    is_proxy: bool = False
    is_hosting: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# Hosting/cloud ASNs and wording, as a second opinion next to the `hosting`
# flag: `ip-api` marks plenty of resold datacenter space as clean.
HOSTING_MARKERS = (
    "hosting", "datacenter", "data center", "cloud", "vps", "server", "colo", "amazon", "digitalocean",
)
# Resold space is announced by a wildcard APNIC/RIPE aggregate (`...-RIPE`,
# `AFRINIC-ERX-...`) instead of the ISP itself. Not disqualified -- just not
# the same thing as an address the ISP hands to a subscriber.
WHOLE_BLOCK_MARKERS = ("AFRINIC-ERX", "-RIPE", "-APNIC")


def grade_proxy(geo: ProxyGeoInfo) -> dict[str, Any]:
    """Turn the raw signals into a verdict a non-specialist can act on.

    Deliberately advisory: it never blocks, because the only real test of a
    proxy is whether the target site accepts it. Signals rank from decisive
    (it is a datacenter) to weak (no reverse DNS anywhere, including on real
    consumer lines).
    """
    reasons: list[str] = []
    score = 100
    haystack = f"{geo.isp} {geo.asn}".lower()
    # Being flagged as hosting is decisive on its own: no combination of good
    # news turns a datacenter address into a home line, so it also floors the
    # grade instead of being another weighted deduction.
    is_datacenter = geo.is_hosting

    if geo.is_hosting:
        score -= 50
        reasons.append("被标记为机房/云（最不适合开号）")
    if any(marker in haystack for marker in HOSTING_MARKERS):
        if not geo.is_hosting:
            score -= 25
        reasons.append("名称像机房服务商")

    if geo.is_proxy:
        score -= 20
        reasons.append("已在公共代理库里（被扫用过，风控更熟）")

    if geo.country_code and geo.country_code.upper() != "US":
        score -= 10
        reasons.append(f"出口在 {geo.country_code}，不是美国")
    if not geo.timezone:
        score -= 10
        reasons.append("拿不到时区，指纹会和出口对不上")

    if any(marker in geo.asn.upper() for marker in WHOLE_BLOCK_MARKERS):
        reasons.append("ASN 是整块转租段（非运营商直售，可接受但要观察）")

    grade = "good" if score >= 80 else "ok" if score >= 55 else "bad"
    if is_datacenter:
        grade = "bad"
    verdict = {
        "good": "可以用",
        "ok": "能用，但有缺点",
        "bad": "不建议用",
    }[grade]
    return {"grade": grade, "score": max(0, score), "verdict": verdict, "reasons": reasons}


def locale_for_country(country_code: str | None) -> str:
    if not country_code:
        return FALLBACK_LOCALE
    return LOCALE_BY_COUNTRY.get(country_code.upper(), FALLBACK_LOCALE)


class ProxyInspector:
    def __init__(self, database: Database, *, timeout: float = 8.0) -> None:
        self._db = database
        self._timeout = timeout

    async def inspect(self, proxy_url: str, expected_ip: str | None = None) -> ProxyGeoInfo:
        """Query the geo endpoint *through* the proxy and validate the result."""
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=self._timeout, trust_env=False) as client:
                response = await client.get(GEO_ENDPOINT)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProxyKillSwitchError(f"proxy timed out: {exc}", reason="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProxyKillSwitchError(f"proxy unreachable: {exc}", reason="unreachable") from exc
        except ValueError as exc:
            raise ProxyKillSwitchError(f"geo lookup returned non-JSON: {exc}", reason="bad_payload") from exc

        if payload.get("status") != "success":
            raise ProxyKillSwitchError(
                f"geo lookup failed: {payload.get('message', 'unknown')}", reason="geo_failed"
            )

        actual_ip = str(payload.get("query") or "").strip()
        if not actual_ip:
            raise ProxyKillSwitchError("geo lookup returned no egress IP", reason="no_ip")

        if expected_ip and actual_ip != expected_ip.strip():
            raise ProxyKillSwitchError(
                f"egress IP drift: expected {expected_ip}, got {actual_ip}", reason="ip_drift"
            )

        timezone = str(payload.get("timezone") or "")
        if not timezone:
            raise ProxyKillSwitchError("geo lookup returned no timezone", reason="no_timezone")

        return ProxyGeoInfo(
            ip=actual_ip,
            country_code=str(payload.get("countryCode") or "").upper(),
            timezone=timezone,
            latitude=float(payload.get("lat") or 0.0),
            longitude=float(payload.get("lon") or 0.0),
            locale=locale_for_country(payload.get("countryCode")),
            region=str(payload.get("regionName") or ""),
            city=str(payload.get("city") or ""),
            isp=str(payload.get("isp") or ""),
            asn=str(payload.get("as") or ""),
            is_proxy=bool(payload.get("proxy")),
            is_hosting=bool(payload.get("hosting")),
        )

    async def verify_and_inspect(self, profile_id: str) -> ProxyGeoInfo:
        """Profile-scoped variant: marks the row PROXY_DEAD on any failure."""
        profile = await self._db.get_profile(profile_id)
        if profile is None:
            raise ProxyKillSwitchError(f"profile not found: {profile_id}", profile_id=profile_id)

        try:
            geo = await self.inspect(profile["upstream_proxy_url"], profile["expected_ip"])
        except ProxyKillSwitchError as exc:
            exc.profile_id = profile_id
            await self._db.set_status(profile_id, "PROXY_DEAD")
            await export_secrets(self._db)
            raise

        await self._db.save_geo(profile_id, _json_dumps(geo.to_json()))
        return geo

    async def diagnose(self, proxy_url: str, expected_ip: str | None = None) -> dict[str, Any]:
        """Inspect without raising, so a parked profile can still be diagnosed.

        `verify_and_inspect` is the kill switch: it raises and parks the row.
        The console needs the opposite -- a verdict for a proxy that may well
        be broken, delivered without touching the stored status.
        """
        try:
            # Always probe without expected_ip so a reachable proxy whose exit IP
            # changed still reports its real IP and reason="ip_drift" below.
            geo = await self.inspect(proxy_url, None)
        except ProxyKillSwitchError as exc:
            return {
                "ok": False,
                "reason": exc.reason,
                "detail": _human_error(exc),
                "grade": "unreachable",
                "score": 0,
                "verdict": "连不上",
                "reasons": ["代理不通，先确认地址/账号密码/端口"],
                "ip": "",
                "expected_ip": expected_ip or "",
            }
        drifted = bool(expected_ip) and geo.ip != expected_ip.strip()
        return {
            "ok": True,
            "reason": "ip_drift" if drifted else "ok",
            "detail": "" if not drifted else f"出口是 {geo.ip}，不是登记的 {expected_ip}",
            "ip": geo.ip,
            "expected_ip": expected_ip or "",
            "isp": geo.isp,
            "asn": geo.asn,
            "country": geo.country_code,
            "city": geo.city,
            "timezone": geo.timezone,
            **grade_proxy(geo),
        }


def _human_error(exc: ProxyKillSwitchError) -> str:
    """Reason codes are stable; the wording is for whoever reads the console."""
    return {
        "timeout": "连接超时，代理没响应",
        "unreachable": "连不上代理（地址/端口/账号密码可能有错）",
        "geo_failed": "代理通了，但探测接口拒绝返回信息（常被限流，隔几秒重试）",
        "no_ip": "代理通了，但拿不到出口 IP",
        "bad_payload": "探测接口返回异常，隔几秒重试",
        "no_timezone": "拿不到时区，可能是便宜代理",
    }.get(exc.reason, str(exc))


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))
