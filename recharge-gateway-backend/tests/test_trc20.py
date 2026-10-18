from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import Order, OrderStatus, PaymentEvent, utcnow
from app.services import runtime_settings, trc20_watcher
from app.services.payments import get_provider
from app.services.payments.trc20 import DirectTrc20Provider

WALLET = "T" + "A" * 33
CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


@pytest.fixture(autouse=True)
def _clear_direct_orders(db):
    db.query(Order).filter(Order.payment_provider == "usdt_trc20").delete()
    db.commit()
    yield


@pytest.fixture()
def direct_config(db):
    runtime_settings.update_settings(
        db,
        {
            "crypto_enabled": True,
            "crypto_provider": "usdt_trc20",
            "usdt_trc20_wallet_address": WALLET,
            "usdt_trc20_contract": CONTRACT,
            "crypto_cny_per_usd": 7.2,
            "crypto_invoice_minutes": 30,
            "crypto_payment_grace_minutes": 15,
        },
    )


def _order(db, amount: str = "13.89", status: OrderStatus = OrderStatus.PENDING) -> Order:
    order = Order(
        order_no=f"MOL{uuid4().hex[:12].upper()}",
        email="trc20@example.com",
        amount=Decimal(amount),
        status=status,
        payment_provider="usdt_trc20",
        payment_currency="USDT",
        payment_amount=Decimal(amount),
        payment_address=WALLET,
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_direct_provider_selects_unique_amount(db, direct_config):
    _order(db, "13.89")
    provider = get_provider("crypto")
    assert isinstance(provider, DirectTrc20Provider)
    amount = provider.select_unique_amount(db, Decimal("100.00"))
    assert amount == Decimal("13.90")
    intent = provider.create_native("MOLTEST", Decimal("100.00"), "测试订单", payment_amount=amount)
    assert intent.provider == "usdt_trc20"
    assert intent.payment_address == WALLET
    assert intent.payment_amount == Decimal("13.90")
    assert intent.payment_currency == "USDT"
    assert intent.expires_in == 1800


def test_trc20_watcher_matches_once_and_marks_paid(db, direct_config, monkeypatch):
    order = _order(db, "13.89")
    now_ms = int(utcnow().replace(tzinfo=timezone.utc).timestamp() * 1000)

    def fake_fetch(api_base, wallet, contract, api_key, min_ts, max_ts):
        assert wallet == WALLET
        return [
            {
                "transaction_id": "tx-trc20-1",
                "token_info": {"address": CONTRACT, "decimals": 6, "symbol": "USDT"},
                "to": WALLET,
                "from": "T" + "B" * 33,
                "type": "Transfer",
                "confirmed": True,
                "value": "13890000",
                "block_timestamp": now_ms,
            }
        ]

    monkeypatch.setattr(trc20_watcher, "_fetch_transfers", fake_fetch)
    assert trc20_watcher.scan_pending_orders(db) == 1
    db.expire_all()
    order = db.get(Order, order.id)
    assert order.status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    assert order.payment_trade_no == "tx-trc20-1"
    assert db.query(PaymentEvent).filter(PaymentEvent.event_id == "tx-trc20-1").count() == 1
    assert trc20_watcher.scan_pending_orders(db) == 0


def test_trc20_watcher_ignores_wrong_amount(db, direct_config, monkeypatch):
    order = _order(db, "13.89")
    now_ms = int(utcnow().replace(tzinfo=timezone.utc).timestamp() * 1000)

    def fake_fetch(api_base, wallet, contract, api_key, min_ts, max_ts):
        return [
            {
                "transaction_id": "tx-trc20-wrong",
                "token_info": {"address": CONTRACT, "decimals": 6},
                "to": WALLET,
                "type": "Transfer",
                "confirmed": True,
                "value": "1000000",
                "block_timestamp": now_ms,
            }
        ]

    monkeypatch.setattr(trc20_watcher, "_fetch_transfers", fake_fetch)
    assert trc20_watcher.scan_pending_orders(db) == 0
    db.expire_all()
    assert db.get(Order, order.id).status == OrderStatus.PENDING


def test_direct_crypto_checkout_and_crypto_check_endpoint(client, db, direct_config, monkeypatch):
    product = client.get("/api/v1/store/products/chatgpt-plus-1m-recharge").json()["product"]
    created = client.post(
        "/api/v1/store/orders",
        json={
            "email": f"trc20-{uuid4().hex[:8]}@example.com",
            "password": "StrongPass!2026",
            "items": [{"product_id": product["id"], "variant_id": product["variants"][0]["id"], "quantity": 1}],
            "payment_method": "crypto",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    order = body["order"]
    headers = {"Authorization": f"Bearer {body['user_token']}"}

    intent = client.post(
        f"/api/v1/store/orders/{order['order_no']}/pay",
        json={"provider": "crypto"},
        headers=headers,
    )
    assert intent.status_code == 200, intent.text
    payment = intent.json()
    assert payment["provider"] == "usdt_trc20"
    assert payment["payment_address"] == WALLET
    assert Decimal(str(payment["payment_amount"])) > 0

    now_ms = int(utcnow().replace(tzinfo=timezone.utc).timestamp() * 1000)
    value = str(int(Decimal(str(payment["payment_amount"])) * 10**6))

    def fake_fetch(api_base, wallet, contract, api_key, min_ts, max_ts):
        return [
            {
                "transaction_id": "tx-api-1",
                "token_info": {"address": CONTRACT, "decimals": 6},
                "to": WALLET,
                "type": "Transfer",
                "confirmed": True,
                "value": value,
                "block_timestamp": now_ms,
            }
        ]

    monkeypatch.setattr(trc20_watcher, "_fetch_transfers", fake_fetch)
    checked = client.post(f"/api/v1/store/orders/{order['order_no']}/crypto-check", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["matched"] is True
    assert checked.json()["order"]["status"] in {"PAID", "DELIVERED"}
