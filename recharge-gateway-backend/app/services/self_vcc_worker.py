"""Channel 2: self-owned VCC cards + Playwright checkout automation.

This module is intentionally defensive. It refuses to run unless the feature
flag is enabled, a clean US residential proxy is verified, and a card is
available. A failed payment immediately suspends the card so the $0.60
failure fee and OpenAI's repeated-decline risk can never compound.

The OpenAI/Stripe UI is an external moving target; selectors are best-effort
and all failures are surfaced as a manual-review state rather than silently
retrying a customer's account.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.services import runtime_settings as runtime
from app.models import SkuType, VccCard, VccStatus
from app.services.crypto import decrypt_text
from app.services.errors import RechargeError
from app.services.imap_3ds_reader import Imap3dsReader
from app.services.session_parser import SessionInfo

logger = logging.getLogger(__name__)

CHATGPT_SKUS = {
    SkuType.CHATGPT_PLUS_1M,
    SkuType.CHATGPT_PLUS_3M,
    SkuType.CHATGPT_PLUS_12M,
    SkuType.CHATGPT_PRO_200,
}


class SelfVccFailure(RechargeError):
    def __init__(self, code: str, message: str, release_code: bool = True, card_id: int | None = None) -> None:
        super().__init__(code, message, release_code)
        self.card_id = card_id


@dataclass
class SelfVccResult:
    card_id: int
    message: str


def check_proxy_health() -> dict:
    proxy_url = runtime.get("proxy_url", settings.proxy_url)
    health_url = runtime.get("proxy_health_url", settings.proxy_health_url)
    required_country = runtime.get("proxy_required_country", settings.proxy_required_country)
    if not proxy_url:
        raise SelfVccFailure(
            "PROXY_NOT_CONFIGURED",
            "自营通道未配置美国住宅代理，任务已中止，激活码已释放",
        )
    try:
        with httpx.Client(timeout=20.0, proxy=proxy_url) as client:
            response = client.get(health_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("Proxy health check failed: %s", exc.__class__.__name__)
        raise SelfVccFailure(
            "PROXY_UNAVAILABLE",
            "美国住宅代理连接异常，任务已中止，激活码已释放",
        ) from exc
    country = str(data.get("country") or data.get("country_code") or data.get("countryCode") or "").upper()
    if country and country != required_country.upper():
        raise SelfVccFailure(
            "PROXY_REGION_MISMATCH",
            f"代理出口地区为 {country}，非美国节点，任务已中止",
        )
    return data


def select_vcc_card(db: Session) -> VccCard | None:
    minimum = Decimal(str(runtime.get_float("self_vcc_min_balance", settings.self_vcc_min_balance)))
    return (
        db.query(VccCard)
        .filter(
            VccCard.status == VccStatus.ACTIVE,
            VccCard.available_balance >= minimum,
            VccCard.current_bind_count < VccCard.max_bind_count,
            VccCard.failed_count == 0,
        )
        .order_by(VccCard.available_balance.desc(), VccCard.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )


def suspend_card(db: Session, card: VccCard) -> None:
    card.failed_count += 1
    card.status = VccStatus.SUSPENDED
    db.add(card)
    db.commit()
    logger.warning("VCC card id=%s suspended after a declined payment", card.id)


def mark_card_success(db: Session, card: VccCard) -> None:
    card.current_bind_count += 1
    card.available_balance = max(Decimal("0"), card.available_balance - Decimal("20.00"))
    if card.current_bind_count >= card.max_bind_count:
        card.status = VccStatus.EXHAUSTED
    db.add(card)
    db.commit()


def _card_data(card: VccCard) -> dict:
    return {
        "number": decrypt_text(card.card_number_enc),
        "cvv": decrypt_text(card.cvv_enc),
        "exp_month": card.exp_month,
        "exp_year": card.exp_year,
        "holder_name": card.holder_name,
        "billing": card.billing_address_json,
    }


def _create_checkout_url(session: SessionInfo) -> str:
    if settings.rdvcc_checkout_url_template:
        return settings.rdvcc_checkout_url_template.replace("{access_token}", session.access_token).replace(
            "{user_id}", session.user_id
        )
    if not session.access_token:
        raise SelfVccFailure("CHECKOUT_CREATE_FAILED", "缺少升级所需的账户凭证", release_code=False)
    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"plan_type": "plus", "checkout_ui_mode": "hosted"}
    try:
        with httpx.Client(timeout=30.0, proxy=runtime.get("proxy_url", settings.proxy_url) or None) as client:
            response = client.post(
                f"{settings.openai_api_base.rstrip('/')}/backend-api/payments/checkout",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"checkout create status {response.status_code}")
        data = response.json()
        url = data.get("url") or data.get("checkout_url") or (data.get("data") or {}).get("url")
        if not url or not str(url).startswith("http"):
            raise RuntimeError("checkout response did not contain a hosted URL")
        return str(url)
    except Exception as exc:
        logger.warning("Unable to create a hosted checkout: %s", exc.__class__.__name__)
        raise SelfVccFailure(
            "CHECKOUT_CREATE_FAILED",
            "自营通道未能创建升级结账页，任务已转人工处理",
            release_code=False,
        ) from exc


class PlaywrightCheckout:
    """Best-effort Stripe/OpenAI hosted checkout driver."""

    def __init__(self, headless: bool | None = None) -> None:
        self.headless = settings.self_vcc_headless if headless is None else headless

    def _fill_first(self, page, selectors: list[str], value: str) -> bool:
        for frame in page.frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector).first
                    if locator.count() > 0 and locator.is_visible():
                        locator.fill(value, timeout=2500)
                        return True
                except Exception:
                    continue
        return False

    def _click_first(self, page, selectors: list[str]) -> bool:
        for frame in page.frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector).first
                    if locator.count() > 0 and locator.is_visible():
                        locator.click(timeout=3000)
                        return True
                except Exception:
                    continue
        return False

    def _visible_code_input(self, page):
        for frame in page.frames:
            for selector in (
                "input[autocomplete='one-time-code']",
                "input[name='code']",
                "input[name='otp']",
                "input[id*='code']",
            ):
                try:
                    locator = frame.locator(selector).first
                    if locator.count() > 0 and locator.is_visible():
                        return locator
                except Exception:
                    continue
        return None

    def _cancel_autorenew(self, page) -> bool:
        """Best-effort cancellation of the next billing cycle after success."""
        if not runtime.get_bool("self_vcc_cancel_autorenew", settings.self_vcc_cancel_autorenew):
            return False
        try:
            page.goto("https://chatgpt.com/#settings/Subscription", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)
            return self._click_first(
                page,
                [
                    "button:has-text('Cancel plan')",
                    "button:has-text('Cancel subscription')",
                    "button:has-text('Cancel')",
                ],
            )
        except Exception:
            logger.warning("Auto-renew cancellation could not be completed; manual review recommended")
            return False

    def run(
        self,
        checkout_url: str,
        card: dict,
        proxy_url: str,
        on_state: Callable[[str], None] | None = None,
    ) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise SelfVccFailure("SELF_VCC_UNAVAILABLE", "自营通道浏览器组件未安装，任务已转人工处理", release_code=False) from exc

        proxy = {"server": proxy_url} if proxy_url else None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                proxy=proxy,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                locale="en-US",
                timezone_id=str(card.get("timezone") or "America/New_York"),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                page.goto(checkout_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2500)

                card_number = card["number"]
                filled = self._fill_first(
                    page,
                    [
                        "input[name='cardnumber']",
                        "input[autocomplete='cc-number']",
                        "input[placeholder*='Card number']",
                    ],
                    card_number,
                )
                self._fill_first(
                    page,
                    ["input[name='exp-date']", "input[autocomplete='cc-exp']", "input[placeholder*='MM']"],
                    f"{card['exp_month']}/{str(card['exp_year'])[-2:]}",
                )
                self._fill_first(
                    page,
                    ["input[name='cvc']", "input[autocomplete='cc-csc']", "input[placeholder*='CVC']"],
                    card["cvv"],
                )
                self._fill_first(
                    page,
                    ["input[name='postal']", "input[autocomplete='postal-code']", "input[name*='zip']"],
                    str(card["billing"].get("postal_code", "")),
                )
                self._fill_first(
                    page,
                    ["input[autocomplete='name']", "input[name='name']", "input[name*='holder']"],
                    card["holder_name"],
                )
                for field, selectors in {
                    "line1": ["input[name='addressLine1']", "input[autocomplete='address-line1']", "input[name*='address1']"],
                    "city": ["input[name='city']", "input[autocomplete='address-level2']"],
                    "state": ["input[name='state']", "input[autocomplete='address-level1']", "select[name='state']"],
                }.items():
                    self._fill_first(page, selectors, str(card["billing"].get(field, "")))
                self._fill_first(
                    page,
                    ["select[name='country']", "input[name='country']", "input[autocomplete='country']"],
                    str(card["billing"].get("country", "")),
                )

                if not filled:
                    body = page.locator("body").inner_text(timeout=5000).lower()
                    if "declined" in body:
                        raise SelfVccFailure("CARD_DECLINED", "支付卡被拒绝，卡片已挂起，任务已转人工处理", card_id=None)
                    raise SelfVccFailure(
                        "CHECKOUT_UI_CHANGED",
                        "未能识别结账页银行卡表单，任务已转人工处理",
                        release_code=False,
                    )

                clicked = self._click_first(
                    page,
                    [
                        "button:has-text('Subscribe')",
                        "button:has-text('Pay')",
                        "button[type='submit']",
                    ],
                )
                if not clicked:
                    raise SelfVccFailure("CHECKOUT_UI_CHANGED", "未能提交支付表单，任务已转人工处理", release_code=False)

                # Give Stripe a moment to raise the 3DS challenge.
                page.wait_for_timeout(5000)
                code_input = None
                for _ in range(24):
                    code_input = self._visible_code_input(page)
                    if code_input:
                        break
                    page.wait_for_timeout(1000)
                if code_input:
                    if on_state:
                        on_state("AWAITING_3DS")
                    reader = Imap3dsReader(user=card.get("three_ds_email") or None)
                    verification = reader.wait_for_code(
                        since=datetime.utcnow() - timedelta(seconds=runtime.get_int("rdvcc_3ds_code_window_seconds", settings.rdvcc_3ds_code_window_seconds)),
                        timeout_seconds=90,
                    )
                    if not verification:
                        raise SelfVccFailure(
                            "AWAITING_3DS_CODE",
                            "已触发 3DS 验证，但未读取到验证码邮件，任务已转人工处理",
                            release_code=False,
                        )
                    code_input.fill(verification.code, timeout=5000)
                    self._click_first(
                        page,
                        ["button:has-text('Submit')", "button:has-text('Verify')", "button[type='submit']"],
                    )

                page.wait_for_timeout(8000)
                body = page.locator("body").inner_text(timeout=5000).lower()
                if "declined" in body or "card was declined" in body:
                    raise SelfVccFailure("CARD_DECLINED", "支付卡被拒绝，卡片已挂起", card_id=None)
                if "success" in body or "thanks" in body or "you're now" in body:
                    cancelled = self._cancel_autorenew(page)
                    return (
                        "自营卡池扣款成功，会员已升级"
                        + ("，已取消次月自动续订" if cancelled else "；次月自动续订需人工确认")
                    )
                raise SelfVccFailure(
                    "AWAITING_MANUAL_REVIEW",
                    "支付已提交但未能确认升级结果，任务已转人工处理",
                    release_code=False,
                )
            finally:
                context.close()
                browser.close()


def run_self_vcc_recharge(
    db: Session,
    session: SessionInfo,
    sku_type: SkuType,
    on_state: Callable[[str], None] | None = None,
) -> SelfVccResult:
    if not runtime.get_bool("self_vcc_enabled", settings.self_vcc_enabled):
        raise SelfVccFailure("SELF_VCC_DISABLED", "自营通道未启用，请使用上游通道")
    if sku_type not in CHATGPT_SKUS:
        raise SelfVccFailure("SELF_VCC_UNSUPPORTED_SKU", "自营通道暂不支持该商品")
    if session.kind != "chatgpt":
        raise SelfVccFailure("SELF_VCC_UNSUPPORTED_SESSION", "自营通道仅支持 ChatGPT Session")

    check_proxy_health()
    card = select_vcc_card(db)
    if card is None:
        raise SelfVccFailure(
            "NO_VCC_CARD_AVAILABLE",
            "自营卡池暂无可用卡片，激活码已释放，请稍后重试",
        )
    card_id = card.id
    data = _card_data(card)
    if runtime.get_bool("self_vcc_dry_run", settings.self_vcc_dry_run):
        raise SelfVccFailure(
            "SELF_VCC_DRY_RUN",
            "自营通道处于演练模式，已停止在提交支付之前，激活码已释放",
            card_id=card_id,
        )

    checkout_url = _create_checkout_url(session)
    try:
        message = PlaywrightCheckout().run(checkout_url, data, runtime.get("proxy_url", settings.proxy_url), on_state=on_state)
    except SelfVccFailure as exc:
        if exc.card_id is None:
            exc.card_id = card_id
        if exc.code == "CARD_DECLINED":
            suspend_card(db, card)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected self-VCC failure")
        raise SelfVccFailure(
            "SELF_VCC_ERROR",
            "自营通道执行异常，任务已转人工处理",
            release_code=False,
            card_id=card_id,
        ) from exc
    mark_card_success(db, card)
    return SelfVccResult(card_id=card_id, message=message)
