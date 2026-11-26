"""Optional delivery notification e-mail with RFC 5322 hygiene and audit logging."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from app.config import settings
from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(
        runtime.get("smtp_host", settings.smtp_host)
        and runtime.get("smtp_user", settings.smtp_user)
        and runtime.get("smtp_password", settings.smtp_password)
    )


def _resolve_from() -> tuple[str, str]:
    """Return ``(envelope_addr, formatted_header)`` safely even if ``smtp_from`` already has a display name."""
    raw = (runtime.get("smtp_from", settings.smtp_from) or "").strip()
    display, addr = parseaddr(raw)
    if not addr:
        addr = (runtime.get("smtp_user", settings.smtp_user) or "").strip()
    header_name = display or settings.brand_name_cn
    header = formataddr((header_name, addr)) if addr else raw
    return addr, header


def _send_message(message: MIMEText, recipients: list[str]) -> bool:
    host = runtime.get("smtp_host", settings.smtp_host)
    port = runtime.get_int("smtp_port", settings.smtp_port)
    user = runtime.get("smtp_user", settings.smtp_user)
    password = runtime.get("smtp_password", settings.smtp_password)
    envelope_from, _ = _resolve_from()
    sender = envelope_from or user
    if runtime.get_bool("smtp_use_ssl", settings.smtp_use_ssl):
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(user, password)
            server.sendmail(sender, recipients, message.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
            server.sendmail(sender, recipients, message.as_string())
    return True


def _log_email_result(event: str, title: str, status: str, error: str = "") -> None:
    try:
        from app.database import SessionLocal
        from app.models import NotificationLog

        with SessionLocal() as db:
            db.add(
                NotificationLog(
                    channel="email",
                    event=event,
                    title=title[:200],
                    status=status,
                    error=error[:500],
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("Could not persist email NotificationLog", exc_info=False)


def send_operator_email(title: str, fields: dict) -> bool:
    """Internal alert to the operator mailbox (falls back to ``smtp_from``)."""
    if not smtp_configured():
        return False
    envelope_from, header_from = _resolve_from()
    raw_recipient = runtime.get("ops_alert_email", settings.ops_alert_email) or envelope_from
    _, recipient = parseaddr(raw_recipient)
    recipient = recipient or raw_recipient.strip()
    if not recipient:
        return False
    lines = [title, ""] + [f"{label}：{value}" for label, value in fields.items()]
    message = MIMEText("\n".join(lines), "plain", "utf-8")
    message["Subject"] = f"【{settings.brand_name_cn}】{title}"
    message["From"] = header_from
    message["To"] = recipient
    try:
        _send_message(message, [recipient])
        _log_email_result("ops.alert_email", title, "sent")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Operator alert email failed: %s", exc.__class__.__name__)
        _log_email_result("ops.alert_email", title, "failed", str(exc))
        return False


def send_delivery_email(email: str, order_payload: dict, *, raise_on_error: bool = False) -> bool:
    if not smtp_configured():
        return False
    _, header_from = _resolve_from()
    order_no = str(order_payload.get("order_no") or "")
    amount = float(order_payload.get("amount") or 0)
    lines = [
        f"{settings.brand_name_cn}（{settings.brand_name_en}）订单已自动交付",
        "",
        f"订单编号：{order_no}",
        f"订单金额：¥{amount:.2f}",
        "",
    ]
    for item in order_payload.get("items", []):
        lines.append(f"商品：{item.get('product_name')} · {item.get('spec_label')}")
        for delivery in item.get("deliveries", []):
            lines.append(f"  交付内容：{delivery.get('payload')}")
        lines.append("")
    lines.extend(
        [
            "请妥善保管以上内容。激活码可在免密自助充能中心完成核销：",
            settings.public_redeem_url,
            "",
            "如遇问题请通过帮助中心联系在线客服。",
            "MOLIPLY 模力补给 · AI 会员 · 软件订阅 · 数字商品",
        ]
    )
    subject = f"【{settings.brand_name_cn}】订单 {order_no} 已自动交付"
    message = MIMEText("\n".join(lines), "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = header_from
    message["To"] = email
    try:
        _send_message(message, [email])
        logger.info("Delivery email sent for order %s", order_no)
        _log_email_result("order.delivery_email", subject, "sent")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delivery email failed for order %s: %s", order_no, exc.__class__.__name__)
        _log_email_result("order.delivery_email", subject, "failed", str(exc))
        if raise_on_error:
            raise
        return False


def send_password_reset_email(email: str, code: str, *, ttl_minutes: int = 10) -> bool:
    """Send a 6-digit password reset verification code to the customer's checkout email."""
    if not smtp_configured():
        return False
    _, header_from = _resolve_from()
    subject = f"【{settings.brand_name_cn}】订单账户密码重置验证码：{code}"
    lines = [
        f"您好，您正在重置 {settings.brand_name_cn}（{settings.brand_name_en}）订单中心登录密码。",
        "",
        f"您的 6 位验证码为：{code}",
        f"有效期：{ttl_minutes} 分钟（请勿泄露给他人）",
        "",
        "如非本人操作，请忽略本邮件，您的账户仍然安全。",
    ]
    message = MIMEText("\n".join(lines), "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = header_from
    message["To"] = email
    try:
        _send_message(message, [email])
        _log_email_result("auth.password_reset_email", subject, "sent")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Password reset email failed for %s: %s", email, exc.__class__.__name__)
        _log_email_result("auth.password_reset_email", subject, "failed", str(exc))
        return False
