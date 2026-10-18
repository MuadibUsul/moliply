"""V2 region routing: Philippines first, US fallback.

The actual OpenAI hosted checkout is an external UI/API. A configured
``RDVCC_CHECKOUT_URL_TEMPLATE`` can be used for controlled testing; otherwise
we ask the ChatGPT checkout API with the selected billing country/currency.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.config import settings
from app.models import RegionPreference, SkuType
from app.services import runtime_settings as runtime
from app.services.session_parser import SessionInfo

logger = logging.getLogger(__name__)

PH = "PH"
US = "US"

REGIONS = {
    PH: {
        "currency": "PHP",
        "country": "PH",
        "proxy_key": "ph_residential_proxy_url",
        "billing_key": "ph",
        "min_balance_key": "self_vcc_ph_min_balance",
        "cost_key": "ph_plus_cost_usd",
    },
    US: {
        "currency": "USD",
        "country": "US",
        "proxy_key": "us_residential_proxy_url",
        "billing_key": "us",
        "min_balance_key": "self_vcc_us_min_balance",
        "cost_key": "us_plus_cost_usd",
    },
}


class RegionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def determine_region(preferred: RegionPreference | str | None, session: SessionInfo) -> str:
    if str(preferred or "").endswith("US_ONLY") or preferred == RegionPreference.US_ONLY:
        return US
    if (session.currency_hint or "").upper() == "USD":
        return US
    return PH


def proxy_for(region: str) -> str:
    spec = REGIONS.get(region, REGIONS[US])
    proxy = runtime.get(spec["proxy_key"], "")
    if not proxy and region == US:
        proxy = runtime.get("proxy_url", settings.proxy_url)
    if not proxy and region == PH:
        proxy = runtime.get("proxy_url", settings.proxy_url)
    if not proxy:
        raise RegionError("PROXY_NOT_CONFIGURED", f"{region} 住宅代理未配置")
    return proxy


def min_balance_for(region: str) -> Decimal:
    spec = REGIONS.get(region, REGIONS[US])
    return Decimal(str(runtime.get_float(spec["min_balance_key"], 22.0)))


def cost_usd_for(region: str) -> Decimal:
    spec = REGIONS.get(region, REGIONS[US])
    return Decimal(str(runtime.get_float(spec["cost_key"], 20.90)))


def amount_for(region: str, sku_type: SkuType) -> tuple[str, Decimal]:
    if sku_type in {SkuType.CHATGPT_PRO_200, SkuType.CHATGPT_PRO_20X_1M}:
        key = "ph_pro_20x_amount_php" if region == PH else "us_pro_20x_amount_usd"
        default = 9990.0 if region == PH else 200.0
    elif sku_type == SkuType.CHATGPT_PLUS_1M:
        key = "ph_plus_amount_php" if region == PH else "us_plus_amount_usd"
        default = 999.0 if region == PH else 20.0
    else:
        raise RegionError("SELF_VCC_UNSUPPORTED_SKU", "该 SKU 暂不支持自营双区充值")
    return REGIONS[region]["currency"], Decimal(str(runtime.get_float(key, default)))


def billing_for(card, region: str) -> dict:
    if region == PH:
        billing = card.ph_billing_address_json
    else:
        billing = card.billing_address_json
    if not billing:
        raise RegionError(
            "BILLING_ADDRESS_MISSING",
            f"该卡片缺少 {region} 账单地址，已跳过",
        )
    return billing


def create_checkout_url(session: SessionInfo, region: str, sku_type: SkuType) -> str:
    currency, amount = amount_for(region, sku_type)
    template = runtime.get("rdvcc_checkout_url_template", settings.rdvcc_checkout_url_template)
    if template:
        return (
            template.replace("{access_token}", session.access_token)
            .replace("{user_id}", session.user_id)
            .replace("{region}", region)
            .replace("{currency}", currency)
            .replace("{amount}", str(amount))
        )

    plan_type = "pro" if sku_type in {SkuType.CHATGPT_PRO_200, SkuType.CHATGPT_PRO_20X_1M} else "plus"
    payload = {
        "plan_type": plan_type,
        "checkout_ui_mode": "hosted",
        "billing_details": {
            "country": REGIONS[region]["country"],
            "currency": currency,
        },
    }
    try:
        with httpx.Client(
            timeout=30.0,
            proxy=proxy_for(region) or None,
        ) as client:
            response = client.post(
                f"{settings.openai_api_base.rstrip('/')}/backend-api/payments/checkout",
                headers={
                    "Authorization": f"Bearer {session.access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            body = response.text[:300].lower()
            code = "REGION_LOCKED" if "currency" in body or "region" in body else "CHECKOUT_CREATE_FAILED"
            raise RegionError(code, f"{region} 结账会话创建失败（HTTP {response.status_code}）")
        data = response.json()
        url = data.get("url") or data.get("checkout_url") or (data.get("data") or {}).get("url")
        if not url or not str(url).startswith("http"):
            raise RegionError("CHECKOUT_CREATE_FAILED", f"{region} 结账地址未返回")
        return str(url)
    except RegionError:
        raise
    except Exception as exc:
        logger.warning("Checkout creation failed for %s: %s", region, exc.__class__.__name__)
        raise RegionError("CHECKOUT_CREATE_FAILED", f"{region} 结账会话创建失败") from exc
