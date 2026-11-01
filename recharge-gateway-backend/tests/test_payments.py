from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.services.payments import FORBIDDEN_TITLE_WORDS, masked_order_title
from app.services.payments.alipay_f2f import _sign_content as alipay_sign_content
from app.services.payments.desensitize import DEFAULT_SUBJECT_PREFIX, DesensitizedBillingSubject
from app.services.payments.wechat_native import HEADERS_KEY, RAW_BODY_KEY

API_V3_KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def rsa_pair():
    """Merchant key pair; the private half signs, the public half verifies."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("utf-8")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    return private_pem, public_pem


def test_billing_subject_never_leaks_sensitive_words():
    assert DEFAULT_SUBJECT_PREFIX == "MOLIPLY-技术开发与算力服务"
    for index in range(200):
        title = masked_order_title(f"MOL20261018{index:06d}")
        lowered = title.lower()
        assert not any(word.lower() in lowered for word in FORBIDDEN_TITLE_WORDS)
        assert title.startswith(DEFAULT_SUBJECT_PREFIX)


def test_billing_subject_always_appends_the_order_number():
    assert masked_order_title("MOL202610180042").endswith("-MOL202610180042")


def test_billing_subject_stays_within_wechat_description_limit():
    prefix = "MOLIPLY-" + "技" * 60
    subject = DesensitizedBillingSubject(order_no="MOL202610180042", prefix=prefix).render()
    assert len(subject.encode("utf-8")) <= 127
    assert subject.endswith("-MOL202610180042")


def test_gateways_tolerate_blank_api_base(monkeypatch, rsa_pair):
    """An empty env value must fall back to the HTTPS default, not fail the build."""
    from app.services import runtime_settings
    from app.services.payments.alipay_f2f import AlipayF2FProvider

    private_pem, public_pem = rsa_pair
    monkeypatch.setattr(settings, "alipay_app_id", "2021000000000000")
    monkeypatch.setattr(settings, "alipay_app_private_key", private_pem)
    monkeypatch.setattr(settings, "alipay_public_key", public_pem)
    original = runtime_settings.get
    monkeypatch.setattr(
        runtime_settings,
        "get",
        lambda key, default="": "" if key == "alipay_api_base" else original(key, default),
    )
    assert AlipayF2FProvider().api_base.startswith("https://")


def test_alipay_signature_roundtrip(monkeypatch, rsa_pair):
    from app.services.payments.alipay_f2f import AlipayF2FProvider

    private_pem, alipay_public_pem = rsa_pair
    monkeypatch.setattr(settings, "alipay_app_id", "2021000000000000")
    monkeypatch.setattr(settings, "alipay_app_private_key", private_pem)
    monkeypatch.setattr(settings, "alipay_public_key", alipay_public_pem)
    provider = AlipayF2FProvider()

    payload = {
        "app_id": "2021000000000000",
        "out_trade_no": "MOL202610180001",
        "total_amount": "158.00",
        "trade_no": "ALI-TRADE-1",
        "trade_status": "TRADE_SUCCESS",
    }
    signed = provider._sign(alipay_sign_content(payload))
    parsed = provider.parse_notify({**payload, "sign": signed, "sign_type": "RSA2"})
    assert parsed.verified is True
    assert parsed.success is True
    assert parsed.order_no == "MOL202610180001"
    assert parsed.amount == Decimal("158.00")

    tampered = provider.parse_notify({**payload, "total_amount": "0.01", "sign": signed})
    assert tampered.verified is False


def test_alipay_rejects_foreign_app_id(monkeypatch, rsa_pair):
    from app.services.payments.alipay_f2f import AlipayF2FProvider

    private_pem, public_pem = rsa_pair
    monkeypatch.setattr(settings, "alipay_app_id", "2021000000000000")
    monkeypatch.setattr(settings, "alipay_app_private_key", private_pem)
    monkeypatch.setattr(settings, "alipay_public_key", public_pem)
    provider = AlipayF2FProvider()
    payload = {
        "app_id": "2021999999999999",
        "out_trade_no": "MOL202610180003",
        "total_amount": "188.00",
        "trade_status": "TRADE_SUCCESS",
    }
    signed = provider._sign(alipay_sign_content(payload))
    assert provider.parse_notify({**payload, "sign": signed}).verified is False


def _wechat_provider(monkeypatch, rsa_pair):
    from app.services.payments.wechat_native import WechatNativeProvider

    private_pem, platform_pem = rsa_pair
    monkeypatch.setattr(settings, "wechat_app_id", "wx0000000000000000")
    monkeypatch.setattr(settings, "wechat_mch_id", "1600000000")
    monkeypatch.setattr(settings, "wechat_mch_serial_no", "SERIAL-1")
    monkeypatch.setattr(settings, "wechat_mch_private_key", private_pem)
    monkeypatch.setattr(settings, "wechat_apiv3_key", API_V3_KEY)
    monkeypatch.setattr(settings, "wechat_platform_public_key", platform_pem)
    return WechatNativeProvider()


def _wechat_payload(provider, order_no: str, total_cents: int, trade_state: str = "SUCCESS") -> dict:
    """Build a genuine V3 callback envelope the way WeChat would send it."""
    plaintext = {
        "appid": provider.app_id,
        "mchid": provider.mch_id,
        "out_trade_no": order_no,
        "transaction_id": "WX-TRADE-1",
        "trade_state": trade_state,
        "amount": {"total": total_cents, "currency": "CNY"},
    }
    nonce = "abcdefghijkl"
    associated_data = "transaction"
    ciphertext = AESGCM(API_V3_KEY.encode()).encrypt(
        nonce.encode(), json.dumps(plaintext).encode(), associated_data.encode()
    )
    raw_body = json.dumps(
        {
            "id": "evt-1",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": nonce,
                "associated_data": associated_data,
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        }
    )
    timestamp = "1750000000"
    message = f"{timestamp}\nnonce-1\n{raw_body}\n".encode()
    signature = provider._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    payload = json.loads(raw_body)
    payload[RAW_BODY_KEY] = raw_body
    payload[HEADERS_KEY] = {
        "wechatpay-timestamp": timestamp,
        "wechatpay-nonce": "nonce-1",
        "wechatpay-signature": base64.b64encode(signature).decode(),
    }
    return payload


def test_wechat_callback_decrypts_and_verifies(monkeypatch, rsa_pair):
    provider = _wechat_provider(monkeypatch, rsa_pair)
    parsed = provider.parse_notify(_wechat_payload(provider, "MOL202610180002", 18800))
    assert parsed.verified is True
    assert parsed.success is True
    assert parsed.order_no == "MOL202610180002"
    assert parsed.amount == Decimal("188.00")


def test_wechat_callback_rejects_tampered_resource(monkeypatch, rsa_pair):
    provider = _wechat_provider(monkeypatch, rsa_pair)
    payload = _wechat_payload(provider, "MOL202610180004", 18800)
    payload["resource"]["ciphertext"] = base64.b64encode(b"tampered-and-long-enough").decode()
    with pytest.raises(Exception):
        provider.parse_notify(payload)


def test_wechat_callback_rejects_bad_platform_signature(monkeypatch, rsa_pair):
    provider = _wechat_provider(monkeypatch, rsa_pair)
    payload = _wechat_payload(provider, "MOL202610180005", 18800)
    payload[HEADERS_KEY]["wechatpay-signature"] = base64.b64encode(b"x" * 256).decode()
    assert provider.parse_notify(payload).verified is False


def test_wechat_callback_rejects_foreign_mchid(monkeypatch, rsa_pair):
    provider = _wechat_provider(monkeypatch, rsa_pair)
    payload = _wechat_payload(provider, "MOL202610180006", 18800)
    provider.mch_id = "1999999999"
    assert provider.parse_notify(payload).verified is False


def _create_pending_order(client, email: str):
    product = client.get("/api/v1/store/products/chatgpt-plus-1m-recharge").json()["product"]
    variant = product["variants"][0]
    created = client.post(
        "/api/v1/store/orders",
        json={
            "email": email,
            "password": "CustomerPass!123",
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
        },
    ).json()
    return created["order"], {"Authorization": f"Bearer {created['user_token']}"}


def _alipay_provider(monkeypatch, rsa_pair):
    from app.services.payments.alipay_f2f import AlipayF2FProvider

    private_pem, public_pem = rsa_pair
    monkeypatch.setattr(settings, "alipay_app_id", "2021000000000000")
    monkeypatch.setattr(settings, "alipay_app_private_key", private_pem)
    monkeypatch.setattr(settings, "alipay_public_key", public_pem)
    monkeypatch.setattr(settings, "payment_mode", "live")
    return AlipayF2FProvider()


def test_alipay_callback_settles_order(client, monkeypatch, rsa_pair):
    provider = _alipay_provider(monkeypatch, rsa_pair)
    order, headers = _create_pending_order(client, "alipay-callback@example.com")

    payload = {
        "app_id": "2021000000000000",
        "out_trade_no": order["order_no"],
        "total_amount": f"{order['amount']:.2f}",
        "trade_no": "ALI-CALLBACK-1",
        "trade_status": "TRADE_SUCCESS",
    }
    payload["sign"] = provider._sign(alipay_sign_content(payload))
    payload["sign_type"] = "RSA2"

    response = client.post("/api/v1/payments/alipay/notify", data=payload)
    assert response.status_code == 200
    assert response.text == "success"

    detail = client.get(f"/api/v1/store/orders/{order['order_no']}", headers=headers).json()["order"]
    assert detail["status"] == "DELIVERED"
    assert detail["payment_label"] == "支付宝"
    assert detail["items"][0]["deliveries"][0]["payload"].startswith("MOLI-")

    duplicate = client.post("/api/v1/payments/alipay/notify", data=payload)
    assert duplicate.status_code == 200
    again = client.get(f"/api/v1/store/orders/{order['order_no']}", headers=headers).json()["order"]
    assert len(again["items"][0]["deliveries"]) == 1


def test_alipay_callback_rejects_wrong_amount(client, monkeypatch, rsa_pair):
    provider = _alipay_provider(monkeypatch, rsa_pair)
    order, _ = _create_pending_order(client, "alipay-amount@example.com")
    payload = {
        "app_id": "2021000000000000",
        "out_trade_no": order["order_no"],
        "total_amount": "0.01",
        "trade_no": "ALI-CALLBACK-2",
        "trade_status": "TRADE_SUCCESS",
    }
    payload["sign"] = provider._sign(alipay_sign_content(payload))
    assert client.post("/api/v1/payments/alipay/notify", data=payload).status_code == 400


def test_wechat_callback_settles_order(client, monkeypatch, rsa_pair):
    provider = _wechat_provider(monkeypatch, rsa_pair)
    order, headers = _create_pending_order(client, "wechat-callback@example.com")

    payload = _wechat_payload(provider, order["order_no"], int(round(float(order["amount"]) * 100)))
    # The signature covers the exact bytes, so post the raw body rather than a re-encoded dict.
    response = client.post(
        "/api/v1/payments/wechat/notify",
        content=payload[RAW_BODY_KEY],
        headers={**payload[HEADERS_KEY], "content-type": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert json.loads(response.text)["code"] == "SUCCESS"

    detail = client.get(f"/api/v1/store/orders/{order['order_no']}", headers=headers).json()["order"]
    assert detail["status"] == "DELIVERED"
    assert detail["payment_label"] == "微信支付"
    assert detail["items"][0]["deliveries"][0]["payload"].startswith("MOLI-")


def test_nowpayments_signature_and_ipn_settlement(client, monkeypatch):
    from app.services.payments.nowpayments import NowPaymentsProvider, ipn_signature

    monkeypatch.setattr(settings, "nowpayments_api_key", "np-key")
    monkeypatch.setattr(settings, "nowpayments_ipn_secret", "np-ipn-secret")
    monkeypatch.setattr(settings, "crypto_enabled", True)

    order, headers = _create_pending_order(client, "crypto-buyer@example.com")
    client.post(
        f"/api/v1/store/orders/{order['order_no']}/pay",
        json={"provider": "crypto"},
        headers=headers,
    )

    provider = NowPaymentsProvider()
    payload = {
        "payment_id": "np-payment-1",
        "payment_status": "finished",
        "pay_currency": "usdttrc20",
        "price_amount": 21.95,
        "price_currency": "usd",
        "order_id": order["order_no"],
        "actually_paid": 21.95,
    }
    signature = ipn_signature(payload, "np-ipn-secret")
    parsed = provider.parse_notify(payload, signature)
    assert parsed.verified is True
    assert parsed.success is True
    assert parsed.skip_amount_check is True

    waiting_payload = {**payload, "payment_status": "waiting"}
    bad = provider.parse_notify(waiting_payload, ipn_signature(waiting_payload, "np-ipn-secret"))
    assert bad.verified is True
    assert bad.success is False

    response = client.post(
        "/api/v1/payments/nowpayments/ipn",
        json=payload,
        headers={"x-nowpayments-sig": signature},
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/v1/store/orders/{order['order_no']}", headers=headers).json()["order"]
    assert detail["status"] == "DELIVERED"
    assert detail["payment_label"] == "加密货币"
    assert detail["items"][0]["deliveries"][0]["payload"].startswith("MOLI-")
