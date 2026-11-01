"""Optional delivery notification e-mail."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr

from app.config import settings
from app.services import runtime_settings as runtime

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(runtime.get("smtp_host", settings.smtp_host) and runtime.get("smtp_user", settings.smtp_user) and runtime.get("smtp_password", settings.smtp_password))


def _send_message(message: MIMEText, recipients: list[str]) -> bool:
    host = runtime.get("smtp_host", settings.smtp_host)
    port = runtime.get_int("smtp_port", settings.smtp_port)
    user = runtime.get("smtp_user", settings.smtp_user)
    password = runtime.get("smtp_password", settings.smtp_password)
    smtp_from = runtime.get("smtp_from", settings.smtp_from)
    if runtime.get_bool("smtp_use_ssl", settings.smtp_use_ssl):
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(user, password)
            server.sendmail(smtp_from, recipients, message.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
            server.sendmail(smtp_from, recipients, message.as_string())
    return True


def send_operator_email(title: str, fields: dict) -> bool:
    """Internal alert to the operator mailbox (falls back to ``smtp_from``)."""
    if not smtp_configured():
        return False
    recipient = runtime.get("ops_alert_email", settings.ops_alert_email) or runtime.get("smtp_from", settings.smtp_from)
    if not recipient:
        return False
    lines = [title, ""] + [f"{label}：{value}" for label, value in fields.items()]
    message = MIMEText("\n".join(lines), "plain", "utf-8")
    message["Subject"] = f"【{settings.brand_name_cn}】{title}"
    message["From"] = formataddr((settings.brand_name_cn, runtime.get("smtp_from", settings.smtp_from)))
    message["To"] = recipient
    try:
        _send_message(message, [recipient])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Operator alert email failed: %s", exc.__class__.__name__)
        return False


def send_delivery_email(email: str, order_payload: dict) -> bool:
    if not smtp_configured():
        return False
    lines = [
        f"{settings.brand_name_cn}（{settings.brand_name_en}）订单已自动交付",
        "",
        f"订单编号：{order_payload.get('order_no')}",
        f"订单金额：¥{order_payload.get('amount'):.2f}",
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
    message = MIMEText("\n".join(lines), "plain", "utf-8")
    message["Subject"] = f"【{settings.brand_name_cn}】订单 {order_payload.get('order_no')} 已自动交付"
    message["From"] = formataddr((settings.brand_name_cn, runtime.get("smtp_from", settings.smtp_from)))
    message["To"] = email
    try:
        _send_message(message, [email])
        logger.info("Delivery email sent for order %s", order_payload.get("order_no"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delivery email failed: %s", exc.__class__.__name__)
        return False
