"""Configuration and amount helpers for multi-coin direct collection."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from app.config import settings
from app.services import runtime_settings as runtime

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BTC_ADDRESS_RE = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,90}$")

COINS: dict[str, dict[str, object]] = {
    "USDT_TRC20": {
        "label": "USDT-TRC20",
        "currency": "USDT",
        "network": "TRC20",
        "quantum": Decimal("0.01"),
        "rate_kind": "usd",
    },
    "USDC_ERC20": {
        "label": "USDC-ERC20",
        "currency": "USDC",
        "network": "ERC20",
        "quantum": Decimal("0.01"),
        "rate_kind": "usd",
    },
    "ETH": {
        "label": "ETH",
        "currency": "ETH",
        "network": "ETH",
        "quantum": Decimal("0.000001"),
        "rate_kind": "eth",
    },
    "BTC": {
        "label": "BTC",
        "currency": "BTC",
        "network": "BTC",
        "quantum": Decimal("0.00000001"),
        "rate_kind": "btc",
    },
}


def _address(coin: str) -> str:
    if coin == "USDT_TRC20":
        return runtime.get("usdt_trc20_wallet_address", settings.usdt_trc20_wallet_address).strip()
    if coin == "USDC_ERC20":
        return runtime.get("usdc_erc20_wallet_address", settings.usdc_erc20_wallet_address).strip()
    if coin == "ETH":
        return runtime.get("eth_wallet_address", settings.eth_wallet_address).strip()
    if coin == "BTC":
        return runtime.get("btc_wallet_address", settings.btc_wallet_address).strip()
    return ""


def coin_address(coin: str) -> str:
    return _address(coin)


def coin_configured(coin: str) -> bool:
    spec = COINS.get(coin)
    if not spec:
        return False
    address = _address(coin)
    if not address:
        return False
    try:
        if coin_rate(coin) <= 0:
            return False
    except Exception:
        return False
    if coin == "USDT_TRC20":
        return bool(re.match(r"^T[1-9A-HJ-NP-Za-km-z]{33}$", address))
    if coin in {"USDC_ERC20", "ETH"}:
        return bool(EVM_ADDRESS_RE.match(address)) and bool(
            runtime.get("etherscan_api_key", settings.etherscan_api_key).strip()
        )
    if coin == "BTC":
        return bool(BTC_ADDRESS_RE.match(address))
    return False


def enabled_coins() -> list[str]:
    requested = runtime.get_list("direct_crypto_coins", [settings.direct_crypto_coins])
    coins = [coin.strip().upper() for coin in requested]
    return [coin for coin in coins if coin in COINS and coin_configured(coin)]


def coin_label(coin: str) -> str:
    return str(COINS.get(coin, {}).get("label") or coin)


def coin_rate(coin: str) -> Decimal:
    spec = COINS.get(coin) or {}
    kind = spec.get("rate_kind")
    if kind == "btc":
        value = runtime.get_float("crypto_btc_cny_rate", settings.crypto_btc_cny_rate)
    elif kind == "eth":
        value = runtime.get_float("crypto_eth_cny_rate", settings.crypto_eth_cny_rate)
    else:
        value = runtime.get_float("crypto_cny_per_usd", settings.crypto_cny_per_usd)
    return Decimal(str(value or 0))


def coin_amount(cny_amount: Decimal, coin: str, offset: int = 0) -> Decimal:
    spec = COINS.get(coin)
    if not spec:
        raise ValueError("unsupported coin")
    rate = coin_rate(coin)
    if rate <= 0:
        raise ValueError("coin rate is not configured")
    quantum = spec["quantum"]  # type: ignore[assignment]
    base = (Decimal(str(cny_amount)) / rate).quantize(Decimal(quantum), rounding=ROUND_HALF_UP)
    if base < quantum:
        base = Decimal(quantum)
    return (base + Decimal(offset) * Decimal(quantum)).quantize(Decimal(quantum))


def coin_network(coin: str) -> str:
    return str(COINS.get(coin, {}).get("network") or "")


def coin_currency(coin: str) -> str:
    return str(COINS.get(coin, {}).get("currency") or "")
