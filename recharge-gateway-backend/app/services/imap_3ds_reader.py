"""IMAP reader for RDVCC 3DS verification codes.

RDVCC delivers the one-time code to the mailbox registered with the card
provider. Codes are valid for roughly five minutes, so the worker polls the
mailbox for a short window and only accepts messages received *after* the
payment attempt started.
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
from email.utils import parsedate_to_datetime
from typing import Callable

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class VerificationCode:
    code: str
    subject: str
    received_at: datetime


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _message_text(message: Message) -> str:
    chunks: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if content_type == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
                text = html.unescape(text)
            chunks.append(text)
    else:
        payload = message.get_payload(decode=True)
        if payload:
            text = payload.decode(message.get_content_charset() or "utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            chunks.append(text)
    return "\n".join(chunks)


def extract_code_from_raw(
    raw: bytes,
    *,
    sender_filter: str = "",
    code_regex: str = r"\b(\d{6})\b",
    since: datetime | None = None,
    now: datetime | None = None,
) -> VerificationCode | None:
    """Parse a raw RFC822 message and return a recent 6-digit code if present."""
    message = email.message_from_bytes(raw)
    sender = _decode(message.get("From", ""))
    if sender_filter and sender_filter.lower() not in sender.lower():
        return None

    received_at: datetime | None = None
    if message.get("Date"):
        try:
            received_at = parsedate_to_datetime(message["Date"])
            if received_at.tzinfo:
                received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            received_at = None
    if received_at is None:
        received_at = now or datetime.utcnow()

    threshold = since or (datetime.utcnow() - timedelta(minutes=5))
    if received_at < threshold - timedelta(seconds=30):
        return None

    subject = _decode(message.get("Subject", ""))
    body = _message_text(message)
    haystack = f"{subject}\n{body}"
    try:
        pattern = re.compile(code_regex)
    except re.error:
        pattern = re.compile(r"\b(\d{6})\b")

    candidates: list[str] = []
    for match in pattern.finditer(haystack):
        value = match.group(1) if match.groups() else match.group(0)
        if value.isdigit() and len(value) == 6 and not (2015 <= int(value) <= 2035):
            candidates.append(value)
    if not candidates:
        return None
    # Verification e-mails often repeat the code in the subject and body;
    # the last occurrence is the most reliable (after boilerplate).
    return VerificationCode(code=candidates[-1], subject=subject, received_at=received_at)


def _imap_since(value: datetime) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{value.day:02d}-{months[value.month - 1]}-{value.year}"


class Imap3dsReader:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        sender: str | None = None,
        code_regex: str | None = None,
    ) -> None:
        self.host = host or settings.rdvcc_imap_host
        self.port = int(port or settings.rdvcc_imap_port)
        self.user = user or settings.rdvcc_imap_user
        self.password = password or settings.rdvcc_imap_password
        self.sender = sender or settings.rdvcc_3ds_sender
        self.code_regex = code_regex or settings.rdvcc_3ds_code_regex

    def fetch_latest_code(self, since: datetime) -> VerificationCode | None:
        if not (self.host and self.user and self.password):
            return None
        client = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            client.login(self.user, self.password)
            client.select("INBOX")
            criteria: list[str] = []
            if self.sender:
                criteria.extend(["FROM", f'"{self.sender}"'])
            criteria.extend(["SINCE", _imap_since(since - timedelta(days=1))])
            status, data = client.uid("search", None, *criteria)
            if status != "OK" or not data or not data[0]:
                return None
            uids = data[0].split()[-15:]
            found: list[VerificationCode] = []
            for uid in reversed(uids):
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
                code = extract_code_from_raw(
                    raw,
                    sender_filter=self.sender,
                    code_regex=self.code_regex,
                    since=since,
                )
                if code:
                    found.append(code)
            return found[0] if found else None
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    def wait_for_code(
        self,
        since: datetime,
        timeout_seconds: int = 120,
        poll_interval: float = 3.0,
        on_poll: Callable[[int], None] | None = None,
    ) -> VerificationCode | None:
        deadline = time.monotonic() + timeout_seconds
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                code = self.fetch_latest_code(since)
                if code:
                    logger.info("Received 3DS code from %s", self.sender)
                    return code
            except Exception as exc:
                logger.warning("IMAP poll failed: %s", exc.__class__.__name__)
            if on_poll:
                on_poll(attempt)
            time.sleep(poll_interval)
        return None
