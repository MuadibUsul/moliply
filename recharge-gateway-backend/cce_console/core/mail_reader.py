"""Gmail IMAP reader for Claude signup verification codes.

Registration needs the 6-digit code Gmail receives. Each imported account is
supplied with its own Google *app password* (2FA must be on; the account
password cannot be used over IMAP any more).

Only messages newer than the attempt are considered, and the sender must match
Anthropic, so an old code already sitting in the inbox cannot be replayed.
"""

from __future__ import annotations

import email
import html
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Callable

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
# Anthropic signup mail arrives from these; matched as a substring of From.
SENDER_FILTERS = ("anthropic", "claude.ai", "claude.com")
CODE_RE = re.compile(r"\b(\d{6})\b")


class MailError(RuntimeError):
    pass


@dataclass
class VerificationCode:
    code: str
    subject: str
    received_at: datetime


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def _message_text(message: Message) -> str:
    chunks: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if part.get_content_type() == "text/html":
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            chunks.append(text)
    else:
        payload = message.get_payload(decode=True)
        if payload:
            text = payload.decode(message.get_content_charset() or "utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            chunks.append(text)
    return "\n".join(chunks)


def extract_code(
    raw: bytes,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> VerificationCode | None:
    """Return a fresh Anthropic code from one raw RFC822 message, if present.

    Year-like numbers are skipped because a mail body often contains one, and a
    six-digit "2026" would otherwise win.
    """
    message = email.message_from_bytes(raw)
    sender = _decode(message.get("From", "")).lower()
    if not any(marker in sender for marker in SENDER_FILTERS):
        return None

    received_at: datetime | None = None
    if message.get("Date"):
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(message["Date"])
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            received_at = parsed
        except (TypeError, ValueError):
            received_at = None
    if received_at is None:
        received_at = now or datetime.utcnow()

    threshold = since or (datetime.utcnow() - timedelta(minutes=5))
    if received_at < threshold - timedelta(seconds=45):
        return None

    subject = _decode(message.get("Subject", ""))
    haystack = f"{subject}\n{_message_text(message)}"
    candidates = [
        match.group(1)
        for match in CODE_RE.finditer(haystack)
        if not 2015 <= int(match.group(1)) <= 2035
    ]
    if not candidates:
        return None
    return VerificationCode(code=candidates[-1], subject=subject, received_at=received_at)


def _imap_since(value: datetime) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{value.day:02d}-{months[value.month - 1]}-{value.year}"


class GmailReader:
    def __init__(self, address: str, app_password: str) -> None:
        self.address = address
        self.app_password = app_password.replace(" ", "")

    def fetch_latest_code(self, since: datetime) -> VerificationCode | None:
        if not (self.address and self.app_password):
            raise MailError("Gmail 账号或应用专用密码为空")
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        try:
            try:
                client.login(self.address, self.app_password)
            except imaplib.IMAP4.error as exc:
                raise MailError("Gmail 登录失败，请确认已开启两步验证并使用应用专用密码") from exc
            client.select("INBOX")
            status, data = client.uid("search", None, "SINCE", _imap_since(since - timedelta(days=1)))
            if status != "OK" or not data or not data[0]:
                return None
            for uid in reversed(data[0].split()[-12:]):
                status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = None
                for item in fetched:
                    if isinstance(item, tuple) and len(item) >= 2:
                        raw = item[1]
                        break
                if raw is None:
                    continue
                found = extract_code(raw, since=since)
                if found:
                    return found
            return None
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass

    def wait_for_code(
        self,
        since: datetime,
        timeout_seconds: int = 180,
        poll_interval: float = 4.0,
        on_poll: Callable[[int], None] | None = None,
    ) -> VerificationCode | None:
        deadline = time.monotonic() + timeout_seconds
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                code = self.fetch_latest_code(since)
                if code:
                    logger.info("Signup code received for %s", self.address)
                    return code
            except MailError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("IMAP poll failed: %s", exc.__class__.__name__)
            if on_poll:
                on_poll(attempt)
            time.sleep(poll_interval)
        return None


def check_credentials(address: str, app_password: str) -> dict:
    """Validate an app password without fetching mail; used by the import UI."""
    if not (address and app_password):
        return {"ok": False, "error": "账号或应用专用密码为空"}
    client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        try:
            client.login(address, app_password.replace(" ", ""))
        except imaplib.IMAP4.error:
            return {"ok": False, "error": "登录失败：需要应用专用密码，且账号要开两步验证"}
        client.select("INBOX")
        return {"ok": True, "error": ""}
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["GmailReader", "MailError", "VerificationCode", "check_credentials", "extract_code"]
