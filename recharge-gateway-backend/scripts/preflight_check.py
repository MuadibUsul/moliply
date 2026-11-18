#!/usr/bin/env python3
"""One-shot production launch self-check.

Read-only by contract: nothing here writes to the database, charges a card or
sends a message. Every line is a ``[PASS]``/``[WARN]``/``[FAIL]`` verdict with a
one-line reason, and secrets are only ever reported as "set"/"not set".

Run it from the backend directory so the app settings resolve:

    cd recharge-gateway-backend && python scripts/preflight_check.py

Exit code is 0 when nothing failed (warnings allowed), 1 otherwise, so the same
command can gate a release script. ``--strict`` also fails on warnings.

The checks mirror the questions asked immediately before flipping a shop to
live payments:

1. are the three core secrets changed from their shipped defaults and long
   enough;
2. do the payment channels actually sign and decrypt;
3. can *any* fulfilment channel deliver (a card, an upstream API, or the desk);
4. are migrations at head and are Redis/Celery answering.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal

# Allow `python scripts/preflight_check.py` from the backend root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

SHIPPED_DEFAULTS = {
    "admin_password": {"change-me", "", "admin", "password"},
    "admin_jwt_secret": {"dev-admin-jwt-secret-change-me", ""},
    "master_aes_key": {""},
}
# ``.env.example`` uses CHANGE_ME_* placeholders; a copied file then holds a
# non-empty value that is still not a real secret.
PLACEHOLDER_PREFIXES = ("change_me", "changeme", "your_", "replace_me", "todo")
MIN_SECRET_LENGTH = {"admin_password": 12, "admin_jwt_secret": 32, "master_aes_key": 32}

results: list[tuple[str, str, str]] = []


def record(status: str, section: str, check: str, detail: str = "") -> None:
    results.append((status, section, check, detail))


def check_secrets() -> None:
    from app.config import settings

    section = "核心密钥"
    values = {
        "admin_password": settings.admin_password,
        "admin_jwt_secret": settings.admin_jwt_secret,
        "master_aes_key": settings.master_aes_key,
    }
    for key, value in values.items():
        label = {
            "admin_password": "ADMIN_PASSWORD",
            "admin_jwt_secret": "ADMIN_JWT_SECRET",
            "master_aes_key": "MASTER_AES_KEY",
        }[key]
        if not value:
            record(FAIL, section, f"{label} 已配置", "缺失")
            continue
        lowered = value.strip().lower()
        if value in SHIPPED_DEFAULTS[key] or lowered.startswith(PLACEHOLDER_PREFIXES):
            record(FAIL, section, f"{label} 已脱离默认值", "仍在使用示例值")
            continue
        if len(value) < MIN_SECRET_LENGTH[key]:
            record(
                WARN,
                section,
                f"{label} 长度足够",
                f"当前 {len(value)}，建议 ≥ {MIN_SECRET_LENGTH[key]}",
            )
            continue
        record(PASS, section, f"{label} 已加固", f"长度 {len(value)}")

    if key_is_valid_base64_aes(settings.master_aes_key):
        record(PASS, section, "MASTER_AES_KEY 可解码为 32 字节", "")
    else:
        record(FAIL, section, "MASTER_AES_KEY 可解码为 32 字节", "base64 解码后不是 32 字节")

    if not settings.admin_totp_secret:
        record(WARN, section, "后台已启用 TOTP 二次验证", "为空则后台只有密码一道门")
    else:
        record(PASS, section, "后台已启用 TOTP 二次验证", "已配置")


def key_is_valid_base64_aes(value: str) -> bool:
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except Exception:  # noqa: BLE001
        return False


def check_payment() -> None:
    from app.config import settings
    from app.services import runtime_settings as runtime

    section = "支付通道"
    mode = runtime.get("payment_mode", settings.payment_mode)
    if mode != "live":
        record(WARN, section, "支付模式为 live", f"当前 {mode}，上线前需切换")
    else:
        record(PASS, section, "支付模式为 live", "")

    check_alipay()
    check_wechat()
    check_desensitized_subject()


def check_alipay() -> None:
    from app.services.payments.alipay_f2f import AlipayF2FProvider, _sign_content

    section = "支付通道"
    try:
        provider = AlipayF2FProvider()
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "支付宝当面付 RSA2 密钥可加载", f"{exc.__class__.__name__}: {exc}")
        return
    try:
        # A self round-trip: sign a payload, verify it against the configured
        # Alipay public key. Exercises the exact key pair, not just file presence.
        payload = {
            "app_id": provider.app_id,
            "out_trade_no": "PREFLIGHT0001",
            "total_amount": "0.01",
            "subject": "preflight",
        }
        signature = provider._sign(_sign_content(payload))
        if provider._verify(_sign_content(payload), signature):
            record(PASS, section, "支付宝 RSA2 签名可用", "")
        else:
            record(FAIL, section, "支付宝 RSA2 签名可用", "签名无法用公钥验回，确认是商户私钥")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "支付宝 RSA2 签名可用", f"{exc.__class__.__name__}: {exc}")

    if provider.notify_url.startswith("https://"):
        record(PASS, section, "支付宝回调地址为 HTTPS", provider.notify_url)
    else:
        record(FAIL, section, "支付宝回调地址为 HTTPS", provider.notify_url or "未配置")


def check_wechat() -> None:
    from app.services.payments.wechat_native import WechatNativeProvider

    section = "支付通道"
    try:
        provider = WechatNativeProvider()
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "微信支付 V3 配置完整", str(exc))
        return
    record(PASS, section, "微信支付 V3 配置完整", "证书序列号/APIv3 密钥/私钥均已加载")
    try:
        signature = provider._authorization("POST", "/v3/pay/transactions/native", "{}")
        if "WECHATPAY2-SHA256-RSA2048" in signature:
            record(PASS, section, "微信支付请求签名可生成", "")
        else:
            record(FAIL, section, "微信支付请求签名可生成", "Authorization 头格式异常")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "微信支付请求签名可生成", f"{exc.__class__.__name__}: {exc}")


def check_desensitized_subject() -> None:
    from app.services.payments.desensitize import DesensitizedBillingSubject, subject_prefix

    section = "支付通道"
    subject = DesensitizedBillingSubject(order_no="MOL202611180001").render()
    try:
        encoded = subject.encode("utf-8")
    except Exception:  # noqa: BLE001
        record(FAIL, section, "商品名脱敏", "前缀含非法字符")
        return
    if len(encoded) > 127:
        record(FAIL, section, "商品名脱敏", f"{len(encoded)} 字节 > 微信上限 127")
    elif "MOL202611180001" not in subject:
        record(FAIL, section, "商品名脱敏", "订单号在脱敏过程中丢失")
    else:
        record(PASS, section, "商品名脱敏", f"前缀「{subject_prefix()[:20]}…」+ 订单号，{len(encoded)} 字节")


def check_fulfilment() -> None:
    from app.database import SessionLocal
    from app.models import VccStatus, VccCard
    from app.services import channel_dispatcher
    from app.services import runtime_settings as runtime
    from app.config import settings

    section = "履约库存"
    with SessionLocal() as db:
        active_cards = db.query(VccCard).filter(VccCard.status == VccStatus.ACTIVE).count()
    self_enabled = runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled)
    upstream_ok = channel_dispatcher.upstream_configured()

    if active_cards > 0:
        record(PASS, section, "自营卡池有可用卡", f"{active_cards} 张 ACTIVE")
    else:
        record(FAIL, section, "自营卡池有可用卡", "0 张 ACTIVE")

    if not self_enabled:
        record(WARN, section, "自营通道已启用", "self_vcc_enabled=off，卡池不会参与路由")
    elif runtime.get_bool("self_vcc_dry_run", settings.self_vcc_dry_run):
        record(WARN, section, "自营通道已关闭演练模式", "dry_run=on，只演练不扣款")
    else:
        record(PASS, section, "自营通道已启用且非演练", "")

    if upstream_ok:
        record(PASS, section, "上游兜底通道可达", "已配置 API 凭证")
    else:
        record(WARN, section, "上游兜底通道可达", "未配置；卡池空了就只能人工")

    if active_cards > 0 or upstream_ok:
        record(PASS, section, "至少有一条自动履约通道", "")
    else:
        record(FAIL, section, "至少有一条自动履约通道", "卡池与上游都不可用")


def check_infra() -> None:
    from app.config import settings
    from app.database import engine
    from app.services.locks import redis_client

    section = "基础设施"
    if not settings.database_url.startswith("sqlite"):
        record(PASS, section, "数据库不是本地 SQLite", "")
    else:
        record(WARN, section, "数据库不是本地 SQLite", "生产应为 PostgreSQL")

    try:
        heads = alembic_head()
        current = db_revision(engine)
        if current is None:
            record(FAIL, section, "数据库迁移在最新 head", "未找到 alembic_version")
        elif current == heads:
            record(PASS, section, "数据库迁移在最新 head", current)
        else:
            record(FAIL, section, "数据库迁移在最新 head", f"当前 {current}，head {heads}")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "数据库迁移在最新 head", f"{exc.__class__.__name__}: {exc}")

    client = redis_client()
    if client is None:
        record(WARN, section, "Redis 可用", "不可达，限流/锁会退化为进程内")
    else:
        try:
            client.ping()
            record(PASS, section, "Redis 可用", "")
        except Exception:  # noqa: BLE001
            record(FAIL, section, "Redis 可用", "PING 失败")

    if settings.task_mode.lower() == "celery":
        record(PASS, section, "任务模式为 celery", "")
    else:
        record(WARN, section, "任务模式为 celery", f"当前 {settings.task_mode}")

    if settings.task_mode.lower() == "celery":
        check_celery_workers()


def alembic_head() -> str:
    """Head revision from the migration scripts, without touching the DB."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def db_revision(engine) -> str | None:
    from sqlalchemy import text

    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def check_celery_workers() -> None:
    section = "基础设施"
    try:
        out = subprocess.run(
            [sys.executable, "-m", "celery", "-A", "app.tasks.celery_app.celery_app", "inspect", "ping"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if out.returncode == 0 and "pong" in out.stdout.lower():
            record(PASS, section, "Celery Worker 在线", "")
        else:
            record(FAIL, section, "Celery Worker 在线", (out.stderr or out.stdout or "无响应").strip()[:120])
    except Exception as exc:  # noqa: BLE001
        record(FAIL, section, "Celery Worker 在线", f"{exc.__class__.__name__}")


def check_crypto_rules() -> None:
    """A short reminder list: crypto needs an address or it silently stays off."""
    from app.config import settings
    from app.services import runtime_settings as runtime

    section = "加密收款"
    if not runtime.get_bool("crypto_enabled", settings.crypto_enabled):
        record(WARN, section, "加密收款已启用", "关闭中")
        return
    provider = runtime.get("crypto_provider", settings.crypto_provider).lower()
    if provider == "usdt_trc20":
        from app.services import direct_crypto

        coins = direct_crypto.enabled_coins()
        if coins:
            record(PASS, section, "自建直收地址已配置", f"{len(coins)} 个币种")
        else:
            record(FAIL, section, "自建直收地址已配置", "已开启但没有任何可用币种")
    else:
        if settings.nowpayments_api_key:
            record(PASS, section, "NOWPayments 凭证已配置", "")
        else:
            record(FAIL, section, "NOWPayments 凭证已配置", "已开启但缺少 API key")


SECTIONS = (
    ("核心密钥", check_secrets),
    ("支付通道", check_payment),
    ("履约库存", check_fulfilment),
    ("基础履约提醒", check_crypto_rules),
    ("基础设施", check_infra),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MOLIPLY pre-launch self-check (read-only)")
    parser.add_argument("--strict", action="store_true", help="also fail when there are warnings")
    args = parser.parse_args()

    for _section, fn in SECTIONS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - one broken check must not abort the rest
            record(FAIL, "运行器", fn.__name__, f"{exc.__class__.__name__}: {exc}")

    width = max((len(check) for _s, _sec, check, _d in results), default=0)
    current_section = None
    for status, section, check, detail in results:
        if section != current_section:
            current_section = section
            print(f"\n== {section} ==")
        mark = f"[{status}]"
        print(f"{mark:<7}{check:<{width}}  {detail}".rstrip())

    failed = sum(1 for status, *_ in results if status == FAIL)
    warned = sum(1 for status, *_ in results if status == WARN)
    passed = len(results) - failed - warned
    print(f"\n合计：{passed} PASS / {warned} WARN / {failed} FAIL")

    # JSON copy for a release log pipeline.
    if os.getenv("PREFLIGHT_JSON"):
        print(json.dumps([{"status": s, "section": sec, "check": c, "detail": d} for s, sec, c, d in results], ensure_ascii=False))

    if failed or (args.strict and warned):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
