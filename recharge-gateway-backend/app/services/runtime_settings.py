"""Database-backed runtime settings for the admin console.

Environment variables remain the defaults. Values saved here override them at
runtime (with a 2-second per-process cache) so payment/crypto/upstream/support
changes do not require editing .env or rebuilding containers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import PlatformSetting
from app.services.crypto import decrypt_text, encrypt_text
from app.services.notification_events import DEFAULT_FEISHU_EVENTS, FEISHU_EVENT_KEYS

logger = logging.getLogger(__name__)

SECRET_KEYS = {
    "ltzf_key",
    "xunhupay_app_secret",
    "nowpayments_api_key",
    "nowpayments_ipn_secret",
    "upstream_api_key",
    "upstream_api_secret",
    "rdvcc_imap_password",
    "smtp_password",
    "feishu_webhook_url",
    "feishu_secret",
}

BOOL_KEYS = {
    "mock_payment_enabled",
    "crypto_enabled",
    "self_vcc_enabled",
    "self_vcc_dry_run",
    "smtp_use_ssl",
    "enforce_stock",
    "feishu_enabled",
    "feishu_mention_all",
}

INT_KEYS = {
    "upstream_max_wait_seconds",
    "upstream_poll_interval_seconds",
    "rdvcc_imap_port",
    "rdvcc_3ds_code_window_seconds",
    "smtp_port",
    "order_expire_minutes",
    "feishu_low_stock_threshold",
}

FLOAT_KEYS = {
    "crypto_cny_per_usd",
    "self_vcc_min_balance",
}

ENV_DEFAULTS = {
    "payment_mode": lambda: settings.payment_mode,
    "payment_gateway": lambda: "ltzf",
    "mock_payment_enabled": lambda: settings.mock_payment_enabled,
    "ltzf_mch_id": lambda: settings.ltzf_mch_id,
    "ltzf_app_id": lambda: settings.ltzf_app_id,
    "ltzf_api_base": lambda: settings.ltzf_api_base,
    "ltzf_notify_url": lambda: settings.ltzf_notify,
    "xunhupay_app_id": lambda: settings.xunhupay_app_id,
    "xunhupay_api_base": lambda: settings.xunhupay_api_base,
    "xunhupay_notify_url": lambda: settings.xunhupay_notify,
    "xunhupay_return_url": lambda: settings.xunhupay_return,
    "crypto_enabled": lambda: settings.crypto_enabled,
    "crypto_provider": lambda: settings.crypto_provider,
    "nowpayments_api_base": lambda: settings.nowpayments_api_base,
    "nowpayments_ipn_url": lambda: settings.nowpayments_ipn,
    "crypto_cny_per_usd": lambda: settings.crypto_cny_per_usd,
    "crypto_supported_coins": lambda: settings.crypto_supported_coins,
    "upstream_mode": lambda: settings.upstream_mode,
    "upstream_api_base": lambda: settings.upstream_api_base,
    "upstream_max_wait_seconds": lambda: settings.upstream_max_wait_seconds,
    "upstream_poll_interval_seconds": lambda: settings.upstream_poll_interval_seconds,
    "self_vcc_enabled": lambda: settings.self_vcc_enabled,
    "self_vcc_dry_run": lambda: settings.self_vcc_dry_run,
    "self_vcc_min_balance": lambda: settings.self_vcc_min_balance,
    "proxy_url": lambda: settings.proxy_url,
    "proxy_health_url": lambda: settings.proxy_health_url,
    "proxy_required_country": lambda: settings.proxy_required_country,
    "us_bin_allowlist": lambda: settings.us_bin_allowlist,
    "rdvcc_imap_host": lambda: settings.rdvcc_imap_host,
    "rdvcc_imap_port": lambda: settings.rdvcc_imap_port,
    "rdvcc_imap_user": lambda: settings.rdvcc_imap_user,
    "rdvcc_3ds_sender": lambda: settings.rdvcc_3ds_sender,
    "rdvcc_3ds_code_regex": lambda: settings.rdvcc_3ds_code_regex,
    "rdvcc_3ds_code_window_seconds": lambda: settings.rdvcc_3ds_code_window_seconds,
    "support_wechat": lambda: settings.support_wechat,
    "support_hours": lambda: settings.support_hours,
    "smtp_host": lambda: settings.smtp_host,
    "smtp_port": lambda: settings.smtp_port,
    "smtp_user": lambda: settings.smtp_user,
    "smtp_from": lambda: settings.smtp_from,
    "smtp_use_ssl": lambda: settings.smtp_use_ssl,
    "order_expire_minutes": lambda: settings.order_expire_minutes,
    "enforce_stock": lambda: settings.enforce_stock,
    "feishu_enabled": lambda: settings.feishu_enabled,
    "feishu_events": lambda: DEFAULT_FEISHU_EVENTS,
    "feishu_low_stock_threshold": lambda: settings.feishu_low_stock_threshold,
    "feishu_mention_all": lambda: settings.feishu_mention_all,
}

# Secrets also have environment defaults, read through settings attributes.
SECRET_ENV_DEFAULTS = {
    "ltzf_key": lambda: settings.ltzf_key,
    "xunhupay_app_secret": lambda: settings.xunhupay_app_secret,
    "nowpayments_api_key": lambda: settings.nowpayments_api_key,
    "nowpayments_ipn_secret": lambda: settings.nowpayments_ipn_secret,
    "upstream_api_key": lambda: settings.upstream_api_key,
    "upstream_api_secret": lambda: settings.upstream_api_secret,
    "rdvcc_imap_password": lambda: settings.rdvcc_imap_password,
    "smtp_password": lambda: settings.smtp_password,
    "feishu_webhook_url": lambda: settings.feishu_webhook_url,
    "feishu_secret": lambda: settings.feishu_secret,
}

ALLOWED_KEYS = set(ENV_DEFAULTS) | SECRET_KEYS
_ENUM_VALUES = {
    "payment_mode": {"mock", "live"},
    "payment_gateway": {"ltzf", "xunhupay"},
    "crypto_provider": {"nowpayments", "mock"},
    "upstream_mode": {"mock", "http"},
}

_cache: dict[str, str] | None = None
_cache_at = 0.0
_cache_ttl = 2.0
_lock = threading.Lock()


def _defaults() -> dict[str, str]:
    values = {key: str(factory() or "") for key, factory in ENV_DEFAULTS.items()}
    values.update({key: str(factory() or "") for key, factory in SECRET_ENV_DEFAULTS.items()})
    return values


def _load() -> dict[str, str]:
    values = _defaults()
    try:
        with SessionLocal() as db:
            rows = db.query(PlatformSetting).all()
        for row in rows:
            if row.key not in ALLOWED_KEYS:
                continue
            try:
                values[row.key] = decrypt_text(row.value) if row.is_secret else row.value
            except Exception:
                logger.warning("Unable to decrypt setting %s; ignoring stored value", row.key)
    except Exception:
        # The table may not exist yet on the very first startup.
        logger.debug("Runtime settings table unavailable; using environment defaults")
    return values


def _all() -> dict[str, str]:
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if _cache is None or now - _cache_at > _cache_ttl:
            _cache = _load()
            _cache_at = now
        return _cache


def refresh() -> None:
    global _cache
    with _lock:
        _cache = None


def get(key: str, default: str = "") -> str:
    return _all().get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    value = get(key, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(get(key, str(default))))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_list(key: str, default: list[str] | None = None) -> list[str]:
    raw = get(key, "")
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def _serialize(key: str, value: Any) -> str:
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        if text not in {"true", "false"}:
            raise ValueError(f"{key} 必须是 true/false")
        return text
    if key in INT_KEYS:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是整数") from exc
        if number < 0:
            raise ValueError(f"{key} 不能为负数")
        return str(number)
    if key in FLOAT_KEYS:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是数字") from exc
        if number < 0:
            raise ValueError(f"{key} 不能为负数")
        return str(number)
    text = str(value).strip()
    if len(text) > 4096:
        raise ValueError(f"{key} 过长")
    if key in _ENUM_VALUES and text not in _ENUM_VALUES[key]:
        raise ValueError(f"{key} 取值无效")
    if key == "crypto_supported_coins":
        text = ",".join(item.upper() for item in text.split(",") if item.strip())
    if key == "feishu_events":
        items = [item.strip() for item in text.split(",") if item.strip()]
        invalid = [item for item in items if item not in FEISHU_EVENT_KEYS]
        if invalid:
            raise ValueError(f"未知飞书事件：{','.join(invalid)}")
        text = ",".join(dict.fromkeys(items))
    return text


def update_settings(
    db: Session,
    values: dict[str, Any],
    clear_secrets: list[str] | None = None,
    actor: str = "admin",
) -> dict:
    for key in values:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"未知设置项：{key}")
    for key in clear_secrets or []:
        if key not in SECRET_KEYS:
            raise ValueError(f"只能清除密钥类设置：{key}")

    for key, value in values.items():
        if key in SECRET_KEYS:
            if value is None or str(value).strip() == "":
                continue
            encoded = encrypt_text(str(value))
            row = db.get(PlatformSetting, key)
            if row is None:
                row = PlatformSetting(key=key)
            row.value = encoded
            row.is_secret = True
            row.updated_by = actor
            db.add(row)
        else:
            serialized = _serialize(key, value)
            row = db.get(PlatformSetting, key)
            if row is None:
                row = PlatformSetting(key=key)
            row.value = serialized
            row.is_secret = False
            row.updated_by = actor
            db.add(row)

    for key in clear_secrets or []:
        row = db.get(PlatformSetting, key)
        if row is not None:
            db.delete(row)

    db.commit()
    refresh()
    return public_settings()


def _coerce_public(key: str, value: str) -> Any:
    if key in BOOL_KEYS:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if key in INT_KEYS:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
    if key in FLOAT_KEYS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0
    return value


def public_settings() -> dict:
    data = _all()
    values = {key: _coerce_public(key, val) for key, val in data.items() if key not in SECRET_KEYS}
    secrets = {key: bool(data.get(key)) for key in SECRET_KEYS}
    return {
        "values": values,
        "secret_configured": secrets,
        "payment_ready": bool(
            (data.get("payment_gateway") == "xunhupay" and data.get("xunhupay_app_id") and data.get("xunhupay_app_secret"))
            or (data.get("payment_gateway") != "xunhupay" and data.get("ltzf_mch_id") and data.get("ltzf_key"))
        ),
        "crypto_ready": bool(data.get("nowpayments_api_key") and data.get("nowpayments_ipn_secret")),
        "upstream_ready": bool(
            data.get("upstream_mode") == "http" and data.get("upstream_api_base") and data.get("upstream_api_key")
        ),
        "support_ready": bool(data.get("support_wechat") and data.get("support_wechat") != "MOLIPLY-SUPPORT"),
        "feishu_ready": bool(data.get("feishu_webhook_url")),
    }
