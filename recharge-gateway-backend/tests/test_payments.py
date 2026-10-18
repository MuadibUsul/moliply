from __future__ import annotations

from decimal import Decimal

from app.config import settings
from app.services.payments import MASKED_TITLE_POOL, FORBIDDEN_TITLE_WORDS, masked_order_title
from app.services.payments.ltzf import LtzfProvider, _sign as ltzf_sign
from app.services.payments.xunhupay import XunhupayProvider, _sign as xunhupay_sign


def test_masked_order_title_never_contains_sensitive_words():
    assert len(MASKED_TITLE_POOL) == 3
    for index in range(200):
        title = masked_order_title(f"MOL20261018{index:06d}")
        lowered = title.lower()
        assert not any(word.lower() in lowered for word in FORBIDDEN_TITLE_WORDS)
        assert "MOL" in title or "模力补给" in title


def test_ltzf_signature_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "ltzf_mch_id", "mch-test")
    monkeypatch.setattr(settings, "ltzf_key", "ltzf-secret")
    provider = LtzfProvider(channel="wechat")
    payload = {
        "out_trade_no": "MOL202610180001",
        "total_fee": "158.00",
        "transaction_id": "LTZF-TRADE-1",
        "trade_status": "SUCCESS",
    }
    payload["sign"] = ltzf_sign(payload, "ltzf-secret")
    parsed = provider.parse_notify(payload)
    assert parsed.verified is True
    assert parsed.success is True
    assert parsed.order_no == "MOL202610180001"
    assert parsed.amount == Decimal("158.00")

    payload["total_fee"] = "0.01"
    tampered = provider.parse_notify(payload)
    assert tampered.verified is False


def test_xunhupay_signature_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "xunhupay_app_id", "app-test")
    monkeypatch.setattr(settings, "xunhupay_app_secret", "xunhu-secret")
    provider = XunhupayProvider()
    payload = {
        "trade_order_id": "MOL202610180002",
        "total_fee": "188.00",
        "transaction_id": "XH-TRADE-1",
        "status": "OD",
    }
    payload["hash"] = xunhupay_sign(payload, "xunhu-secret")
    parsed = provider.parse_notify(payload)
    assert parsed.verified is True
    assert parsed.success is True
    assert parsed.order_no == "MOL202610180002"

    payload["status"] = "WP"
    changed = provider.parse_notify(payload)
    assert changed.verified is False


def test_ltzf_callback_settles_order(client, monkeypatch):
    monkeypatch.setattr(settings, "ltzf_mch_id", "mch-test")
    monkeypatch.setattr(settings, "ltzf_key", "ltzf-secret")
    product = client.get("/api/v1/store/products/chatgpt-plus-1m-recharge").json()["product"]
    variant = product["variants"][0]
    order = client.post(
        "/api/v1/store/orders",
        json={
            "email": "callback@example.com",
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
        },
    ).json()["order"]

    payload = {
        "out_trade_no": order["order_no"],
        "total_fee": f"{order['amount']:.2f}",
        "transaction_id": "LTZF-CALLBACK-1",
        "trade_status": "SUCCESS",
    }
    payload["sign"] = ltzf_sign(payload, "ltzf-secret")

    response = client.post("/api/v1/payments/ltzf/notify", data=payload)
    assert response.status_code == 200
    assert response.text == "SUCCESS"

    detail = client.get(f"/api/v1/store/orders/{order['order_no']}").json()["order"]
    assert detail["status"] == "DELIVERED"
    assert detail["items"][0]["deliveries"][0]["payload"].startswith("MOLI-")

    # Duplicate callback is accepted but must not issue another code.
    duplicate = client.post("/api/v1/payments/ltzf/notify", data=payload)
    assert duplicate.status_code == 200
    detail2 = client.get(f"/api/v1/store/orders/{order['order_no']}").json()["order"]
    assert len(detail2["items"][0]["deliveries"]) == 1
