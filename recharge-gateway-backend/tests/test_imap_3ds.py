from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from app.services.imap_3ds_reader import extract_code_from_raw


def _raw(sender="RDVCC <no-reply@rdvcc.com>", date=None, subject="RDVCC verification code", body="Your RDVCC verification code is 482913. It expires in 5 minutes."):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "owner@example.com"
    message["Subject"] = subject
    message["Date"] = format_datetime(date or datetime.now(timezone.utc))
    message.set_content(body)
    return message.as_bytes()


def test_extract_current_code():
    now = datetime.utcnow()
    result = extract_code_from_raw(_raw(), sender_filter="rdvcc", since=now - timedelta(minutes=1), now=now)
    assert result is not None
    assert result.code == "482913"


def test_rejects_old_message():
    now = datetime.utcnow()
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    result = extract_code_from_raw(_raw(date=old), sender_filter="rdvcc", since=now - timedelta(minutes=1), now=now)
    assert result is None


def test_rejects_other_sender():
    now = datetime.utcnow()
    result = extract_code_from_raw(_raw(sender="Spam <spam@example.com>"), sender_filter="rdvcc", since=now - timedelta(minutes=1), now=now)
    assert result is None


def test_ignores_year_like_numbers():
    now = datetime.utcnow()
    raw = _raw(body="Reference 2026. Your code is 731204. Expires soon.")
    result = extract_code_from_raw(raw, sender_filter="rdvcc", since=now - timedelta(minutes=1), now=now)
    assert result is not None
    assert result.code == "731204"


def test_html_message():
    now = datetime.utcnow()
    message = EmailMessage()
    message["From"] = "RDVCC <no-reply@rdvcc.com>"
    message["Subject"] = "Verification"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content("<html><body>Your code: <b>904221</b></body></html>", subtype="html")
    result = extract_code_from_raw(message.as_bytes(), sender_filter="rdvcc", since=now - timedelta(minutes=1), now=now)
    assert result is not None
    assert result.code == "904221"
