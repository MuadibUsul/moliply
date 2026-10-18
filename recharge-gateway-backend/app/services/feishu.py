"""Feishu custom-bot notifications for admin operations.

The webhook URL and signing secret are edited in the admin console and stored
encrypted in platform_settings. Delivery is best-effort and runs off the
request path so a webhook outage never blocks payments or delivery.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import NotificationLog
from app.services import net_guard
from app.services import runtime_settings as runtime
from app.services.notification_events import FEISHU_EVENTS

logger = logging.getLogger(__name__)

TEMPLATES = {
    "info": "blue",
    "success": "green",
    "warning": "orange",
    "error": "red",
}


def _feishu_signature(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _admin_link(link_path: str | None) -> str:
    if not link_path:
        return ""
    if link_path.startswith("http://") or link_path.startswith("https://"):
        return link_path
    return f"{settings.public_store_url.rstrip('/')}/{link_path.lstrip('/')}"


def _build_card(
    title: str,
    fields: dict[str, Any],
    severity: str,
    link_path: str | None,
    mention_all: bool,
) -> dict:
    lines = [f"{label}：{value}" for label, value in fields.items() if value not in (None, "")]
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines) or "无详细信息"}}
    ]
    if mention_all and severity in {"warning", "error"}:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "<at id=all></at> 请及时处理"}})
    url = _admin_link(link_path)
    if url:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看运营后台"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": TEMPLATES.get(severity, "blue"),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def _log(event: str, title: str, status: str, error: str = "") -> None:
    try:
        with SessionLocal() as db:
            db.add(
                NotificationLog(
                    channel="feishu",
                    event=event,
                    title=title[:200],
                    status=status,
                    error=error[:500],
                )
            )
            db.commit()
    except Exception:
        logger.debug("Unable to write notification log", exc_info=False)


def send_feishu(
    event: str,
    title: str,
    fields: dict[str, Any],
    severity: str = "info",
    link_path: str | None = "/admin",
    *,
    force: bool = False,
) -> dict:
    enabled = runtime.get_bool("feishu_enabled", settings.feishu_enabled)
    webhook = runtime.get("feishu_webhook_url", settings.feishu_webhook_url).strip()
    secret = runtime.get("feishu_secret", settings.feishu_secret).strip()
    if not force:
        if not enabled:
            return {"status": "disabled"}
        subscribed = runtime.get_list("feishu_events", [])
        if event not in subscribed:
            return {"status": "unsubscribed"}
    if not webhook:
        _log(event, title, "failed", "Feishu webhook URL is empty")
        return {"status": "failed", "error": "webhook missing"}
    try:
        net_guard.ensure_https(webhook)
    except ValueError:
        _log(event, title, "failed", "Feishu webhook must use HTTPS")
        return {"status": "failed", "error": "webhook must use HTTPS"}

    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": _build_card(
            title,
            fields,
            severity,
            link_path,
            runtime.get_bool("feishu_mention_all", settings.feishu_mention_all),
        ),
    }
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _feishu_signature(timestamp, secret)

    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post(webhook, json=payload)
        data = response.json() if response.content else {}
        code = data.get("code", data.get("StatusCode", 0))
        if response.status_code >= 400 or str(code) != "0":
            message = str(data.get("msg") or data.get("StatusMessage") or f"HTTP {response.status_code}")
            _log(event, title, "failed", message)
            return {"status": "failed", "error": message}
        _log(event, title, "sent")
        return {"status": "sent"}
    except Exception as exc:
        _log(event, title, "failed", exc.__class__.__name__)
        logger.warning("Feishu notification failed: %s", exc.__class__.__name__)
        return {"status": "failed", "error": exc.__class__.__name__}


def dispatch_event(
    event: str,
    title: str,
    fields: dict[str, Any],
    severity: str = "info",
    link_path: str | None = "/admin",
) -> None:
    """Fire-and-forget so payment/delivery paths are never blocked by Feishu."""
    if not runtime.get_bool("feishu_enabled", settings.feishu_enabled):
        return
    payload = dict(fields)
    threading.Thread(
        target=send_feishu,
        args=(event, title, payload, severity, link_path),
        kwargs={"force": False},
        daemon=True,
    ).start()


def send_test_message(actor: str = "admin") -> dict:
    result = send_feishu(
        "test",
        "MOLIPLY 飞书通知测试",
        {
            "测试时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "操作人": actor,
            "说明": "如果你收到这条消息，说明飞书机器人配置正常。",
        },
        severity="info",
        link_path="/admin/settings",
        force=True,
    )
    return result


def recent_logs(db: Session, limit: int = 12) -> list[dict]:
    rows = db.query(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "event": row.event,
            "title": row.title,
            "status": row.status,
            "error": row.error,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


def event_options() -> list[dict[str, str]]:
    return FEISHU_EVENTS
