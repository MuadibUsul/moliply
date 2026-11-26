"""Dual-track virtual credit card (VCC) API issuers for CCE.

Supports two production tracks so an operator can start immediately with a
USDT-funded cross-border VCC platform (Track A) while waiting for a US EIN, and
switch to or run alongside a US BaaS issuer (Track B: Lithic) by setting env
vars — with zero code changes:

* **Track B — Lithic (`LITHIC_API_KEY`)**:
  Official Lithic REST API (`POST /v1/cards`, `PATCH /v1/cards/{token}`).
  Defaults to `SINGLE_USE` cards with a per-transaction spend limit ($21-$22).
* **Track A — Cross-border / USDT VCC platforms (`VCC_API_URL` + `VCC_API_KEY`)**:
  Compatible with VMCard, FotonCard, Buvei, and any custom REST issuer endpoint
  that accepts JSON and returns card credentials (`pan`/`card_number`, `cvv`,
  `exp_month`/`exp_year` or `exp`). Supports optional HMAC-SHA256 signing via
  `VCC_API_SECRET`.

Also provides US state -> residential postal code alignment (`zip_for_geo`) so
the billing ZIP sent to Stripe matches the account's static residential IP.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger("cce.vcc")

# Representative residential ZIP codes per US state/region for AVS alignment.
# Tax-free states (OR 97204, DE 19801, MT 59601, NH 03101) are preferred when
# the proxy geo does not specify a state.
US_STATE_ZIPS: dict[str, str] = {
    "alabama": "35203",
    "alaska": "99501",
    "arizona": "85004",
    "arkansas": "72201",
    "california": "94107",
    "colorado": "80202",
    "connecticut": "06103",
    "delaware": "19801",
    "district of columbia": "20001",
    "florida": "33131",
    "georgia": "30303",
    "hawaii": "96813",
    "idaho": "83702",
    "illinois": "60601",
    "indiana": "46204",
    "iowa": "50309",
    "kansas": "66101",
    "kentucky": "40202",
    "louisiana": "70112",
    "maine": "04101",
    "maryland": "21202",
    "massachusetts": "02110",
    "michigan": "48226",
    "minnesota": "55401",
    "mississippi": "39201",
    "missouri": "63101",
    "montana": "59601",
    "nebraska": "68102",
    "nevada": "89101",
    "new hampshire": "03101",
    "new jersey": "07102",
    "new mexico": "87102",
    "new york": "10001",
    "north carolina": "28202",
    "north dakota": "58102",
    "ohio": "43215",
    "oklahoma": "73102",
    "oregon": "97204",
    "pennsylvania": "19103",
    "rhode island": "02903",
    "south carolina": "29201",
    "south dakota": "57104",
    "tennessee": "37203",
    "texas": "75201",
    "utah": "84101",
    "vermont": "05401",
    "virginia": "20147",
    "washington": "98101",
    "west virginia": "25301",
    "wisconsin": "53202",
    "wyoming": "82001",
}


class VccIssuerError(RuntimeError):
    pass


@dataclass
class IssuedCard:
    provider: str  # "lithic" | "category_a"
    external_id: str
    pan: str
    cvv: str
    exp_month: str
    exp_year: str
    billing_zip: str
    holder: str
    balance_usd: float


def zip_for_geo(geo: dict[str, Any] | None, fallback: str = "") -> str:
    """Pick a US postal code matching the proxy's state/region for Stripe AVS."""
    default_zip = (fallback or os.environ.get("CCE_DEFAULT_BILLING_ZIP") or "97204").strip()
    if not geo or not isinstance(geo, dict):
        return default_zip
    postal = str(geo.get("postal") or geo.get("zip") or "").strip()
    if re.fullmatch(r"\d{5}", postal):
        return postal
    region = str(geo.get("region") or geo.get("state") or geo.get("city") or "").strip().lower()
    if region in US_STATE_ZIPS:
        return US_STATE_ZIPS[region]
    for state_name, zip_code in US_STATE_ZIPS.items():
        if state_name in region:
            return zip_code
    return default_zip


US_BILLING_ADDRESSES: dict[str, dict[str, str]] = {
    "97204": {"line1": "742 SW Morrison St", "city": "Portland", "state": "OR", "zip": "97204"},
    "19801": {"line1": "1201 N Market St", "city": "Wilmington", "state": "DE", "zip": "19801"},
    "94107": {"line1": "450 Townsend St", "city": "San Francisco", "state": "CA", "zip": "94107"},
    "10001": {"line1": "350 5th Ave", "city": "New York", "state": "NY", "zip": "10001"},
    "75201": {"line1": "2100 Ross Ave", "city": "Dallas", "state": "TX", "zip": "75201"},
    "98101": {"line1": "1420 5th Ave", "city": "Seattle", "state": "WA", "zip": "98101"},
    "20147": {"line1": "1800 Tysons Blvd", "city": "McLean", "state": "VA", "zip": "20147"},
    "33131": {"line1": "701 Brickell Ave", "city": "Miami", "state": "FL", "zip": "33131"},
    "60601": {"line1": "200 E Randolph St", "city": "Chicago", "state": "IL", "zip": "60601"},
}


def billing_profile_for_zip(postal_code: str, holder_name: str = "") -> dict[str, str]:
    """Return a complete US AVS billing address matching the postal code."""
    clean_zip = (postal_code or "97204").strip()
    template = US_BILLING_ADDRESSES.get(clean_zip, US_BILLING_ADDRESSES["97204"])
    name = (holder_name or os.environ.get("VCC_HOLDER_NAME") or "ALEX MERCER").strip()
    if ":" in name or name in {"lithic", "api", "manual"}:
        name = "ALEX MERCER"
    return {
        "holder_name": name,
        "line1": template["line1"],
        "city": template["city"],
        "state": template["state"],
        "zip": clean_zip,
        "country": "US",
    }


def active_provider() -> str | None:
    """Resolve which VCC issuer track is currently active ('lithic', 'category_a', or None)."""
    forced = (os.environ.get("CCE_VCC_PROVIDER") or "auto").strip().lower()
    has_lithic = bool((os.environ.get("LITHIC_API_KEY") or "").strip())
    has_cat_a = bool(
        (os.environ.get("VCC_API_URL") or "").strip()
        and (os.environ.get("VCC_API_KEY") or "").strip()
    )
    if forced == "lithic":
        return "lithic" if has_lithic else None
    if forced in {"category_a", "vmcard", "fotoncard", "buvei", "generic"}:
        return "category_a" if has_cat_a else None
    if forced in {"none", "off", "disabled", "manual"}:
        return None
    if has_lithic:
        return "lithic"
    if has_cat_a:
        return "category_a"
    return None


def issuer_configured() -> bool:
    return active_provider() is not None


def auto_mint_enabled() -> bool:
    raw = (os.environ.get("CCE_VCC_AUTO_MINT") or "true").strip().lower()
    return issuer_configured() and raw not in {"0", "false", "no", "off"}


def auto_close_enabled() -> bool:
    raw = (os.environ.get("CCE_VCC_AUTO_CLOSE") or "true").strip().lower()
    return issuer_configured() and raw not in {"0", "false", "no", "off"}


def issuer_status() -> dict[str, Any]:
    provider = active_provider()
    if provider == "lithic":
        sandbox = (os.environ.get("LITHIC_SANDBOX") or "").strip().lower() in {"1", "true", "yes"}
        label = "Lithic 企业发卡 API（沙盒）" if sandbox else "Lithic 企业发卡 API（B类·美区直连）"
    elif provider == "category_a":
        brand = (os.environ.get("VCC_PROVIDER_NAME") or "A类跨境发卡平台 API").strip()
        label = f"{brand}（USDT 卡台直连）"
    else:
        label = "未配置 API（使用手动卡池）"
    return {
        "configured": provider is not None,
        "provider": provider,
        "label": label,
        "auto_mint": auto_mint_enabled(),
        "auto_close": auto_close_enabled(),
        "default_amount_usd": float(os.environ.get("CCE_VCC_AMOUNT_USD") or "21.0"),
    }


def _lithic_base_url() -> str:
    override = (os.environ.get("LITHIC_API_BASE") or "").strip().rstrip("/")
    if override:
        return override
    sandbox = (os.environ.get("LITHIC_SANDBOX") or "").strip().lower() in {"1", "true", "yes"}
    return "https://sandbox.lithic.com/v1" if sandbox else "https://api.lithic.com/v1"


def _parse_exp_parts(data: dict[str, Any]) -> tuple[str, str]:
    month = str(
        data.get("exp_month")
        or data.get("expMonth")
        or data.get("expiry_month")
        or data.get("month")
        or ""
    ).strip()
    year = str(
        data.get("exp_year")
        or data.get("expYear")
        or data.get("expiry_year")
        or data.get("year")
        or ""
    ).strip()
    if month and year:
        return month.zfill(2), year[-2:]
    combined = str(data.get("exp") or data.get("expiry") or data.get("expiration") or "").strip()
    digits = re.sub(r"\D", "", combined)
    if len(digits) >= 4:
        return digits[:2], digits[-2:]
    raise VccIssuerError("发卡平台返回的数据缺少有效期（exp_month / exp_year）")


async def _mint_lithic(
    *,
    memo: str,
    amount_usd: float,
    billing_zip: str,
) -> IssuedCard:
    api_key = (os.environ.get("LITHIC_API_KEY") or "").strip()
    if not api_key:
        raise VccIssuerError("未配置 LITHIC_API_KEY")
    card_type = (os.environ.get("LITHIC_CARD_TYPE") or "SINGLE_USE").strip().upper()
    duration = (os.environ.get("LITHIC_SPEND_DURATION") or "TRANSACTION").strip().upper()
    spend_limit_cents = max(100, int(round(float(amount_usd) * 100)))
    payload: dict[str, Any] = {
        "type": card_type,
        "memo": (memo or "CCE-Claude-Pro")[:64],
        "spend_limit": spend_limit_cents,
        "spend_limit_duration": duration,
        "state": "OPEN",
    }
    account_token = (os.environ.get("LITHIC_ACCOUNT_TOKEN") or "").strip()
    if account_token:
        payload["account_token"] = account_token
    program_token = (os.environ.get("LITHIC_PROGRAM_TOKEN") or "").strip()
    if program_token:
        payload["card_program_token"] = program_token

    async with httpx.AsyncClient(base_url=_lithic_base_url(), timeout=20.0) as client:
        resp = await client.post(
            "/cards",
            headers={"Authorization": api_key, "Accept": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        detail = resp.text[:240]
        raise VccIssuerError(f"Lithic 开卡失败（HTTP {resp.status_code}）：{detail}")
    data = resp.json()
    pan = str(data.get("pan") or "").strip()
    cvv = str(data.get("cvv") or "").strip()
    token = str(data.get("token") or "").strip()
    if not pan or not cvv:
        raise VccIssuerError("Lithic 返回的数据不含卡号或 CVV（请确认 API Key 具备读取 PAN 权限）")
    exp_month, exp_year = _parse_exp_parts(data)
    return IssuedCard(
        provider="lithic",
        external_id=token,
        pan=pan,
        cvv=cvv,
        exp_month=exp_month,
        exp_year=exp_year,
        billing_zip=billing_zip,
        holder=f"lithic:{token}" if token else "lithic",
        balance_usd=float(amount_usd),
    )


async def _mint_category_a(
    *,
    memo: str,
    amount_usd: float,
    billing_zip: str,
) -> IssuedCard:
    url = (os.environ.get("VCC_API_URL") or "").strip()
    api_key = (os.environ.get("VCC_API_KEY") or "").strip()
    api_secret = (os.environ.get("VCC_API_SECRET") or "").strip()
    card_bin = (os.environ.get("VCC_CARD_BIN") or "").strip()
    if not url or not api_key:
        raise VccIssuerError("未配置 VCC_API_URL 或 VCC_API_KEY")

    ts = str(int(time.time()))
    payload: dict[str, Any] = {
        "amount": round(float(amount_usd), 2),
        "balance": round(float(amount_usd), 2),
        "currency": "USD",
        "memo": (memo or "CCE-Claude-Pro")[:64],
        "label": (memo or "CCE-Claude-Pro")[:64],
        "billing_zip": billing_zip,
        "postal_code": billing_zip,
        "holder_name": (os.environ.get("VCC_HOLDER_NAME") or "ALEX MERCER").strip(),
    }
    if card_bin:
        payload["card_bin"] = card_bin
        payload["bin"] = card_bin

    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Api-Key": api_key,
        "X-Timestamp": ts,
    }
    if api_secret:
        sig_payload = f"{ts}.{raw_body}".encode("utf-8")
        headers["X-Signature"] = hmac.new(
            api_secret.encode("utf-8"), sig_payload, hashlib.sha256
        ).hexdigest()

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, headers=headers, content=raw_body.encode("utf-8"))
    if resp.status_code >= 400:
        raise VccIssuerError(f"A类发卡台开卡失败（HTTP {resp.status_code}）：{resp.text[:240]}")

    root = resp.json()
    if isinstance(root, dict) and root.get("code") not in (None, 0, 200, "0", "200", "OK", "ok", "SUCCESS"):
        if not any(k in root for k in ("pan", "card_number", "data", "card", "result")):
            raise VccIssuerError(f"A类发卡台返回错误：{root.get('msg') or root.get('message') or root}")

    node: dict[str, Any] = root if isinstance(root, dict) else {}
    for subkey in ("data", "card", "result"):
        sub = node.get(subkey)
        if isinstance(sub, dict):
            node = sub
            break

    pan = str(
        node.get("pan")
        or node.get("card_number")
        or node.get("cardNumber")
        or node.get("card_no")
        or node.get("number")
        or ""
    ).strip()
    cvv = str(
        node.get("cvv")
        or node.get("cvc")
        or node.get("cvv2")
        or node.get("security_code")
        or ""
    ).strip()
    ext_id = str(
        node.get("card_id")
        or node.get("cardId")
        or node.get("id")
        or node.get("token")
        or ""
    ).strip()
    if not pan or not cvv:
        raise VccIssuerError("A类发卡台返回的数据缺少卡号（pan/card_number）或安全码（cvv）")
    exp_month, exp_year = _parse_exp_parts(node)
    returned_zip = str(node.get("billing_zip") or node.get("postal_code") or billing_zip).strip()
    return IssuedCard(
        provider="category_a",
        external_id=ext_id,
        pan=pan,
        cvv=cvv,
        exp_month=exp_month,
        exp_year=exp_year,
        billing_zip=returned_zip,
        holder=f"api:{ext_id}" if ext_id else "api",
        balance_usd=float(node.get("balance") or node.get("amount") or amount_usd),
    )


async def mint_card(
    *,
    memo: str = "CCE-Claude-Pro",
    amount_usd: float | None = None,
    billing_zip: str = "",
    geo: dict[str, Any] | None = None,
) -> IssuedCard:
    """Issue a new virtual card through whichever track (B: Lithic or A: USDT REST) is active."""
    provider = active_provider()
    if provider is None:
        raise VccIssuerError("尚未配置自动发卡 API（LITHIC_API_KEY 或 VCC_API_URL + VCC_API_KEY）")
    target_amount = float(
        amount_usd
        if amount_usd is not None
        else (os.environ.get("CCE_VCC_AMOUNT_USD") or "21.0")
    )
    resolved_zip = zip_for_geo(geo, fallback=billing_zip)
    if provider == "lithic":
        return await _mint_lithic(memo=memo, amount_usd=target_amount, billing_zip=resolved_zip)
    return await _mint_category_a(memo=memo, amount_usd=target_amount, billing_zip=resolved_zip)


async def close_remote_card(holder_tag: str) -> bool:
    """Best-effort close/terminate a remote card after single-use charge or hard decline."""
    if not holder_tag or ":" not in holder_tag or not auto_close_enabled():
        return False
    prefix, ext_id = holder_tag.split(":", 1)
    ext_id = ext_id.strip()
    if not ext_id:
        return False
    try:
        if prefix == "lithic":
            api_key = (os.environ.get("LITHIC_API_KEY") or "").strip()
            if not api_key:
                return False
            async with httpx.AsyncClient(base_url=_lithic_base_url(), timeout=15.0) as client:
                resp = await client.patch(
                    f"/cards/{ext_id}",
                    headers={"Authorization": api_key, "Accept": "application/json"},
                    json={"state": "CLOSED"},
                )
                return resp.status_code < 300
        if prefix == "api":
            close_tpl = (os.environ.get("VCC_CLOSE_URL") or "").strip()
            api_key = (os.environ.get("VCC_API_KEY") or "").strip()
            if not close_tpl or not api_key:
                return False
            url = close_tpl.replace("{id}", ext_id).replace("{card_id}", ext_id)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "X-Api-Key": api_key},
                    json={"card_id": ext_id, "state": "CLOSED"},
                )
                return resp.status_code < 300
    except Exception:  # noqa: BLE001
        logger.debug("Best-effort remote card close skipped for %s", holder_tag, exc_info=True)
    return False


def _run_coro_sync(coro):
    """Run an async issuer coroutine safely from synchronous MOLIPLY workers or FastAPI threads."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=30)


def mint_vcc_card(
    *,
    spend_limit_usd: float = 22.0,
    zip_code: str = "97204",
    memo: str = "MOLIPLY-VCC",
) -> dict[str, Any] | None:
    """Synchronous adapter around :func:`mint_card` for MOLIPLY's VccCard pool scheduler."""
    if not issuer_configured():
        return None
    try:
        issued: IssuedCard = _run_coro_sync(
            mint_card(memo=memo, amount_usd=spend_limit_usd, billing_zip=zip_code)
        )
    except Exception:  # noqa: BLE001
        logger.warning("Synchronous VCC mint failed", exc_info=True)
        return None
    return {
        "provider": "LITHIC" if issued.provider == "lithic" else "TRACK_A",
        "pan": issued.pan,
        "cvv": issued.cvv,
        "exp_month": issued.exp_month,
        "exp_year": issued.exp_year,
        "zip_code": issued.billing_zip,
        "external_id": issued.external_id,
        "holder_tag": issued.holder,
        "balance_usd": issued.balance_usd,
    }


def close_remote_card_sync(provider: str, external_id: str) -> bool:
    """Synchronous adapter around :func:`close_remote_card` for MOLIPLY's VccCard pool scheduler."""
    if not external_id:
        return False
    tag = external_id if ":" in external_id else (
        f"lithic:{external_id}" if provider.lower().startswith("lithic") else f"api:{external_id}"
    )
    try:
        return bool(_run_coro_sync(close_remote_card(tag)))
    except Exception:  # noqa: BLE001
        logger.debug("Synchronous remote card close skipped for %s", tag, exc_info=True)
        return False
