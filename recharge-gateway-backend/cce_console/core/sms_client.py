"""Automated US Non-VoIP SMS verification client for CCE Claude signups.

Bridges CCE's account registration flow with MOLIPLY's Non-VoIP SMS providers
(SMSPool and 5sim). When ``claude.ai`` prompts for phone verification after the
email OTP step, CCE automatically leases a US physical-carrier number, submits
it, polls for the 6-digit SMS code, and cancels/refunds the number if no SMS
arrives within the window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("cce.sms")

OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


@dataclass
class SmsLease:
    provider: str  # "smspool" | "fivesim"
    order_id: str
    phone: str  # E.164 or 10-digit US number


def _smspool_key() -> str:
    key = (os.environ.get("SMSPOOL_API_KEY") or "").strip()
    if key:
        return key
    try:
        from app.services import runtime_settings as runtime

        return (runtime.get("smspool_api_key", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _fivesim_key() -> str:
    key = (os.environ.get("FIVESIM_API_KEY") or "").strip()
    if key:
        return key
    try:
        from app.services import runtime_settings as runtime

        return (runtime.get("fivesim_api_key", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def sms_configured() -> bool:
    return bool(_smspool_key() or _fivesim_key())


def _normalize_us_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


async def lease_us_number(service: str = "claude") -> SmsLease | None:
    """Lease one US Non-VoIP phone number from SMSPool (preferred) or 5sim."""
    smspool = _smspool_key()
    if smspool:
        try:
            async with httpx.AsyncClient(base_url="https://api.smspool.net", timeout=20.0) as client:
                resp = await client.post(
                    "/purchase/sms",
                    data={"key": smspool, "country": "US", "service": service, "quantity": 1},
                )
            if resp.status_code < 400:
                body = resp.json()
                order_id = body.get("order_id") or body.get("orderid")
                number = body.get("number") or body.get("phonenumber")
                if order_id and number:
                    return SmsLease("smspool", str(order_id), _normalize_us_phone(str(number)))
        except Exception:  # noqa: BLE001
            logger.warning("SMSPool lease failed; checking fallback", exc_info=False)

    fivesim = _fivesim_key()
    if fivesim:
        try:
            async with httpx.AsyncClient(base_url="https://5sim.net/v1", timeout=20.0) as client:
                resp = await client.get(
                    f"/user/buy/activation/usa/any/{service}",
                    headers={"Authorization": f"Bearer {fivesim}", "Accept": "application/json"},
                )
            if resp.status_code < 400:
                body = resp.json()
                order_id = body.get("id")
                number = body.get("phone")
                if order_id and number:
                    return SmsLease("fivesim", str(order_id), _normalize_us_phone(str(number)))
        except Exception:  # noqa: BLE001
            logger.warning("5sim lease failed", exc_info=False)
    return None


async def poll_sms_code(lease: SmsLease, *, timeout_seconds: int = 110, interval: float = 4.0) -> str | None:
    """Poll the leased number until a 6-digit OTP arrives or timeout expires."""
    loops = max(1, int(timeout_seconds / max(1.0, interval)))
    for _ in range(loops):
        await asyncio.sleep(interval)
        try:
            if lease.provider == "smspool":
                async with httpx.AsyncClient(base_url="https://api.smspool.net", timeout=15.0) as client:
                    resp = await client.post(
                        "/sms/check",
                        data={"key": _smspool_key(), "orderid": lease.order_id},
                    )
                if resp.status_code < 400:
                    body = resp.json()
                    text = str(body.get("sms") or body.get("code") or "")
                    match = OTP_RE.search(text)
                    if match:
                        return match.group(1)
            elif lease.provider == "fivesim":
                async with httpx.AsyncClient(base_url="https://5sim.net/v1", timeout=15.0) as client:
                    resp = await client.get(
                        f"/user/check/{lease.order_id}",
                        headers={"Authorization": f"Bearer {_fivesim_key()}", "Accept": "application/json"},
                    )
                if resp.status_code < 400:
                    body = resp.json()
                    for msg in body.get("sms") or []:
                        text = str(msg.get("code") or msg.get("text") or "")
                        match = OTP_RE.search(text)
                        if match:
                            return match.group(1)
        except Exception:  # noqa: BLE001
            continue
    return None


async def cancel_lease(lease: SmsLease) -> None:
    """Return an unused phone number to the supplier so no balance is lost."""
    try:
        if lease.provider == "smspool":
            async with httpx.AsyncClient(base_url="https://api.smspool.net", timeout=15.0) as client:
                await client.post("/sms/cancel", data={"key": _smspool_key(), "orderid": lease.order_id})
        elif lease.provider == "fivesim":
            async with httpx.AsyncClient(base_url="https://5sim.net/v1", timeout=15.0) as client:
                await client.get(
                    f"/user/cancel/{lease.order_id}",
                    headers={"Authorization": f"Bearer {_fivesim_key()}", "Accept": "application/json"},
                )
    except Exception:  # noqa: BLE001
        pass
