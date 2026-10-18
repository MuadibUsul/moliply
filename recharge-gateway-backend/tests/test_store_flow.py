from __future__ import annotations

PASSWORD = "CustomerPass!123"


def _product(client, slug):
    response = client.get(f"/api/v1/store/products/{slug}")
    assert response.status_code == 200, response.text
    return response.json()["product"]


def _place_order(
    client,
    slug="chatgpt-plus-1m-recharge",
    email="buyer@example.com",
    coupon=None,
    password=PASSWORD,
):
    product = _product(client, slug)
    variant = product["variants"][0]
    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": email,
            "password": password,
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
            "coupon_code": coupon,
            "payment_method": "wechat",
        },
    )
    return product, variant, response


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


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


def test_order_payment_delivery_and_account_management(client):
    _, _, response = _place_order(client, coupon="MOLI10")
    assert response.status_code == 201, response.text
    data = response.json()
    order = data["order"]
    token = data["user_token"]
    assert data["account_created"] is True
    assert data["user_email"] == "buyer@example.com"
    assert order["status"] == "PENDING"
    assert order["amount"] == 148.0
    assert order["discount_amount"] == 10.0
    assert order["items"][0]["deliveries"] == []

    headers = _headers(token)
    assert client.get(f"/api/v1/store/orders/{order['order_no']}").status_code == 401
    detail = client.get(f"/api/v1/store/orders/{order['order_no']}", headers=headers)
    assert detail.status_code == 200

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "buyer@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    login_token = login.json()["token"]
    me = client.get("/api/v1/auth/me", headers=_headers(login_token))
    assert me.status_code == 200
    assert me.json()["email"] == "buyer@example.com"

    payment = client.post(
        f"/api/v1/store/orders/{order['order_no']}/pay",
        json={"provider": "wechat"},
        headers=headers,
    )
    assert payment.status_code == 200, payment.text
    assert payment.json()["mock"] is True

    settled = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle", headers=headers)
    assert settled.status_code == 200, settled.text
    delivered = settled.json()["order"]
    assert delivered["status"] == "DELIVERED"
    payload = delivered["items"][0]["deliveries"][0]["payload"]
    assert payload.startswith("MOLI-")

    my_orders = client.get("/api/v1/store/my/orders", headers=headers)
    assert my_orders.status_code == 200
    assert any(item["order_no"] == order["order_no"] for item in my_orders.json()["items"])

    # Duplicate settlement must be idempotent and must not issue another code.
    again = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle", headers=headers)
    assert again.status_code == 200
    assert len(again.json()["order"]["items"][0]["deliveries"]) == 1


def test_existing_email_requires_the_same_password(client):
    email = "reuse@example.com"
    _, _, first = _place_order(client, email=email)
    assert first.status_code == 201, first.text

    _, _, wrong = _place_order(client, email=email, password="WrongPass!999")
    assert wrong.status_code == 401
    assert "密码" in wrong.json()["detail"]

    _, _, correct = _place_order(client, email=email)
    assert correct.status_code == 201
    assert correct.json()["account_created"] is False


def test_orders_are_scoped_to_their_account(client):
    _, _, response_a = _place_order(client, email="owner@example.com")
    assert response_a.status_code == 201
    order_a = response_a.json()["order"]

    _, _, response_b = _place_order(client, email="other@example.com")
    assert response_b.status_code == 201
    token_b = response_b.json()["user_token"]

    forbidden = client.get(
        f"/api/v1/store/orders/{order_a['order_no']}",
        headers=_headers(token_b),
    )
    assert forbidden.status_code == 404
    my_orders = client.get("/api/v1/store/my/orders", headers=_headers(token_b)).json()["items"]
    assert all(item["order_no"] != order_a["order_no"] for item in my_orders)


def test_coupon_validation(client):
    ok = client.post("/api/v1/store/coupons/validate", json={"code": "MOLI10", "amount": 200})
    assert ok.status_code == 200
    assert ok.json()["discount"] == 10.0

    rejected = client.post("/api/v1/store/coupons/validate", json={"code": "MOLI10", "amount": 20})
    assert rejected.status_code == 400

    invalid = client.post("/api/v1/store/coupons/validate", json={"code": "NOPE", "amount": 200})
    assert invalid.status_code == 400


def test_account_delivery(client):
    product, _, response = _place_order(
        client,
        slug="claude-pro-account",
        email="account-buyer@example.com",
    )
    assert response.status_code == 201
    data = response.json()
    assert product["delivery_type"] == "ACCOUNT_CREDENTIALS"
    settled = client.post(
        f"/api/v1/store/orders/{data['order']['order_no']}/mock-settle",
        headers=_headers(data["user_token"]),
    )
    assert settled.status_code == 200
    delivered = settled.json()["order"]
    assert delivered["status"] == "DELIVERED"
    payload = delivered["items"][0]["deliveries"][0]["payload"]
    assert "----" in payload
    assert "@" in payload


def test_unpaid_order_hides_delivery_payload(client):
    _, _, response = _place_order(client, email="pending@example.com")
    data = response.json()
    detail = client.get(
        f"/api/v1/store/orders/{data['order']['order_no']}",
        headers=_headers(data["user_token"]),
    ).json()["order"]
    assert detail["status"] == "PENDING"
    assert detail["items"][0]["deliveries"] == []


def test_unknown_order_is_not_revealed(client):
    _, _, response = _place_order(client, email="search@example.com")
    token = response.json()["user_token"]
    missing = client.get("/api/v1/store/orders/MOL99999999999999", headers=_headers(token))
    assert missing.status_code == 404
