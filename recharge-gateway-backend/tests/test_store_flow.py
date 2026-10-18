from __future__ import annotations


def _product(client, slug):
    response = client.get(f"/api/v1/store/products/{slug}")
    assert response.status_code == 200, response.text
    return response.json()["product"]


def _place_order(client, slug="chatgpt-plus-1m-recharge", email="buyer@example.com", coupon=None):
    product = _product(client, slug)
    variant = product["variants"][0]
    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": email,
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
            "coupon_code": coupon,
            "payment_method": "wechat",
        },
    )
    assert response.status_code == 201, response.text
    return product, variant, response.json()["order"]


def test_catalogue_and_categories(client):
    products = client.get("/api/v1/store/products").json()
    assert products["total"] >= 10
    assert len(products["categories"]) == 6
    detail = _product(client, "chatgpt-plus-1m-recharge")
    assert len(detail["variants"]) == 3
    assert detail["delivery_type"] == "ACTIVATION_CODE"


def test_product_detail_accepts_numeric_id(client):
    products = client.get("/api/v1/store/products").json()["items"]
    product = products[0]
    response = client.get(f"/api/v1/store/products/{product['id']}")
    assert response.status_code == 200
    assert response.json()["product"]["id"] == product["id"]


def test_order_payment_delivery_and_lookup(client):
    _, _, order = _place_order(client, coupon="MOLI10")
    assert order["status"] == "PENDING"
    assert order["amount"] == 148.0
    assert order["discount_amount"] == 10.0
    assert order["items"][0]["deliveries"] == []

    payment = client.post(f"/api/v1/store/orders/{order['order_no']}/pay", json={"provider": "wechat"})
    assert payment.status_code == 200, payment.text
    assert payment.json()["mock"] is True
    assert "MOLI" in payment.json()["qr_content"] or "pay/" in payment.json()["qr_content"]

    settled = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle")
    assert settled.status_code == 200, settled.text
    delivered = settled.json()["order"]
    assert delivered["status"] == "DELIVERED"
    assert delivered["stock_pending"] is False
    payload = delivered["items"][0]["deliveries"][0]["payload"]
    assert payload.startswith("MOLI-")

    lookup = client.get(f"/api/v1/store/orders/{order['order_no']}")
    assert lookup.status_code == 200
    assert lookup.json()["order"]["items"][0]["deliveries"][0]["payload"] == payload

    by_email = client.get("/api/v1/store/orders", params={"q": "buyer@example.com"})
    assert by_email.status_code == 200
    assert any(item["order_no"] == order["order_no"] for item in by_email.json()["items"])

    # Second settlement must be idempotent and must not issue a second code.
    again = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle")
    assert again.status_code == 200
    assert len(again.json()["order"]["items"][0]["deliveries"]) == 1


def test_coupon_validation(client):
    ok = client.post("/api/v1/store/coupons/validate", json={"code": "MOLI10", "amount": 200})
    assert ok.status_code == 200
    assert ok.json()["discount"] == 10.0

    rejected = client.post("/api/v1/store/coupons/validate", json={"code": "MOLI10", "amount": 20})
    assert rejected.status_code == 400

    invalid = client.post("/api/v1/store/coupons/validate", json={"code": "NOPE", "amount": 200})
    assert invalid.status_code == 400


def test_account_delivery(client):
    product, _, order = _place_order(client, slug="claude-pro-account", email="account-buyer@example.com")
    assert product["delivery_type"] == "ACCOUNT_CREDENTIALS"
    settled = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle")
    assert settled.status_code == 200
    delivered = settled.json()["order"]
    assert delivered["status"] == "DELIVERED"
    payload = delivered["items"][0]["deliveries"][0]["payload"]
    assert "----" in payload
    assert "@" in payload


def test_unpaid_order_hides_delivery_payload(client):
    _, _, order = _place_order(client, email="pending@example.com")
    detail = client.get(f"/api/v1/store/orders/{order['order_no']}").json()["order"]
    assert detail["status"] == "PENDING"
    assert detail["items"][0]["deliveries"] == []


def test_order_not_found(client):
    response = client.get("/api/v1/store/orders", params={"q": "MOL99999999999999"})
    assert response.status_code == 404
