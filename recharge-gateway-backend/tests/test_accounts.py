from __future__ import annotations

from app.services.accounts import hash_password, verify_password


def test_password_hash_roundtrip():
    encoded = hash_password("CustomerPass!123")
    assert encoded.startswith("scrypt$")
    assert "CustomerPass!123" not in encoded
    assert verify_password("CustomerPass!123", encoded) is True
    assert verify_password("WrongPass!999", encoded) is False


def _create_order_account(client, email, password="CustomerPass!123"):
    product = client.get("/api/v1/store/products/chatgpt-plus-1m-recharge").json()["product"]
    variant = product["variants"][0]
    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": email,
            "password": password,
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_change_password_invalidates_old_token(client):
    data = _create_order_account(client, "change-pass@example.com")
    old_token = data["user_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=old_headers,
        json={"old_password": "CustomerPass!123", "new_password": "NewCustomerPass!456"},
    )
    assert changed.status_code == 200, changed.text
    new_token = changed.json()["token"]

    expired = client.get("/api/v1/store/my/orders", headers=old_headers)
    assert expired.status_code == 401

    fresh = client.get("/api/v1/store/my/orders", headers={"Authorization": f"Bearer {new_token}"})
    assert fresh.status_code == 200

    old_login = client.post(
        "/api/v1/auth/login",
        json={"email": "change-pass@example.com", "password": "CustomerPass!123"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/v1/auth/login",
        json={"email": "change-pass@example.com", "password": "NewCustomerPass!456"},
    )
    assert new_login.status_code == 200


def test_admin_can_reset_customer_password(client, admin_headers):
    data = _create_order_account(client, "reset-pass@example.com")
    old_token = data["user_token"]

    reset = client.post(
        "/api/v1/admin/users/reset-password",
        headers=admin_headers,
        json={"email": "reset-pass@example.com", "new_password": "ResetPass!789"},
    )
    assert reset.status_code == 200, reset.text

    invalidated = client.get(
        "/api/v1/store/my/orders",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert invalidated.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "reset-pass@example.com", "password": "ResetPass!789"},
    )
    assert login.status_code == 200
