from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import Order, OrderStatus, PaymentEvent, utcnow
from app.services import btc_watcher, direct_crypto, evm_watcher, runtime_settings
from app.services.payments.direct_multi import DirectBitcoinProvider, DirectEthereumProvider

EVM_WALLET = "0x" + "a" * 40
BTC_WALLET = "bc1q" + "a" * 38


@pytest.fixture()
def multi_config(db):
    runtime_settings.update_settings(
        db,
        {
            "crypto_enabled": True,
            "crypto_provider": "usdt_trc20",
            "direct_crypto_coins": "USDT_TRC20,USDC_ERC20,ETH,BTC",
            "usdc_erc20_wallet_address": EVM_WALLET,
            "usdc_erc20_contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "eth_wallet_address": EVM_WALLET,
            "btc_wallet_address": BTC_WALLET,
            "etherscan_api_base": "https://api.etherscan.io/v2/api",
            "etherscan_api_key": "test-etherscan",
            "btc_api_base": "https://blockstream.info/api",
            "crypto_cny_per_usd": 7.2,
            "crypto_eth_cny_rate": 20000,
            "crypto_btc_cny_rate": 500000,
            "eth_confirmations": 12,
            "btc_confirmations": 2,
        },
    )


def _order(db, *, provider, currency, address, network, amount):
    order = Order(
        order_no=f"MOL{uuid4().hex[:12].upper()}",
        email=f"multi-{uuid4().hex[:6]}@example.com",
        amount=Decimal("100.00"),
        status=OrderStatus.PENDING,
        payment_provider=provider,
        payment_currency=currency,
        payment_amount=Decimal(amount),
        payment_address=address,
        payment_network=network,
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_enabled_coins_only_configured(db, multi_config):
    coins = direct_crypto.enabled_coins()
    assert coins == ["USDC_ERC20", "ETH", "BTC"]  # USDT has no address in this fixture
    usdc = DirectEthereumProvider("USDC_ERC20")
    btc = DirectBitcoinProvider()
    assert usdc.select_unique_amount(db, Decimal("100")) > 0
    assert btc.select_unique_amount(db, Decimal("100")) > 0


def test_evm_watcher_matches_usdc(db, multi_config, monkeypatch):
    order = _order(db, provider="usdc_erc20", currency="USDC", address=EVM_WALLET, network="ERC20", amount="13.89")
    now = int(utcnow().replace(tzinfo=__import__("datetime").timezone.utc).timestamp())
    item = {
        "hash": "0xevm-tx-1",
        "to": EVM_WALLET,
        "from": "0x" + "b" * 40,
        "value": "13890000",
        "tokenDecimal": "6",
        "blockNumber": "100",
        "timeStamp": str(now),
    }
    monkeypatch.setattr(evm_watcher, "_request", lambda api_base, params: [item])
    monkeypatch.setattr(evm_watcher, "_current_block", lambda api_base, api_key: 120)
    assert evm_watcher.scan_pending_orders(db) == 1
    db.expire_all()
    assert db.get(Order, order.id).status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    assert db.query(PaymentEvent).filter(PaymentEvent.event_id == "0xevm-tx-1").count() == 1


def test_btc_watcher_matches_btc(db, multi_config, monkeypatch):
    order = _order(db, provider="btc", currency="BTC", address=BTC_WALLET, network="BTC", amount="0.00020000")

    def fake_json(url):
        if url.endswith("/blocks/tip/height"):
            return 105
        return [
            {
                "txid": "btc-tx-1",
                "status": {"confirmed": True, "block_height": 100, "block_time": int(utcnow().replace(tzinfo=timezone.utc).timestamp())},
                "vout": [
                    {"scriptpubkey_address": "other", "value": 1000},
                    {"scriptpubkey_address": BTC_WALLET, "value": 20000},
                ],
            }
        ]

    monkeypatch.setattr(btc_watcher, "_get_json", fake_json)
    assert btc_watcher.scan_pending_orders(db) == 1
    db.expire_all()
    assert db.get(Order, order.id).status in {OrderStatus.PAID, OrderStatus.DELIVERED}
