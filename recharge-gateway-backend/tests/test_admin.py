from __future__ import annotations

from app.services.code_generator import generate_code


def test_admin_login_rejects_bad_password(client):
    response = client.post("/api/v1/admin/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401


def test_admin_overview(client, admin_headers):
    response = client.get("/api/v1/admin/overview", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["orders_total"] >= 0
    assert len(data["trend"]) == 12
    assert {channel["name"] for channel in data["channels"]} >= {"上游 API 通道", "自营 VCC 通道"}


def test_generate_import_and_disable_codes(client, admin_headers):
    generated = client.post(
        "/api/v1/admin/codes/generate",
        headers=admin_headers,
        json={"count": 3, "sku_type": "CHATGPT_PLUS_1M", "channel_mode": "UPSTREAM", "note": "test"},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["created"] == 3

    local_code = generate_code()
    imported = client.post(
        "/api/v1/admin/codes/import",
        headers=admin_headers,
        json={
            "text": f"{local_code}\n{local_code}\nupstream-card-raw-123",
            "sku_type": "CHATGPT_PLUS_1M",
            "channel_mode": "UPSTREAM",
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 2
    assert imported.json()["skipped"] == 1

    listing = client.get("/api/v1/admin/codes", headers=admin_headers, params={"q": local_code})
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["code"] == local_code

    disabled = client.patch(
        f"/api/v1/admin/codes/{item['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["item"]["status"] == "DISABLED"

    restored = client.patch(
        f"/api/v1/admin/codes/{item['id']}/status",
        headers=admin_headers,
        json={"status": "UNUSED"},
    )
    assert restored.status_code == 200
    assert restored.json()["item"]["status"] == "UNUSED"


def test_add_and_list_vcc_cards(client, admin_headers, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "us_bin_allowlist", "424242")
    created = client.post(
        "/api/v1/admin/cards",
        headers=admin_headers,
        json={
            "provider": "RDVCC",
            "card_number": "4242424242424242",
            "cvv": "123",
            "exp_month": "12",
            "exp_year": "2029",
            "holder_name": "DEMO USER",
            "billing_address": {
                "line1": "1 Main St",
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "US",
            },
            "available_balance": 50,
            "max_bind_count": 1,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["card_masked"] == "4242 **** **** 4242"

    invalid = client.post(
        "/api/v1/admin/cards",
        headers=admin_headers,
        json={
            "provider": "RDVCC",
            "card_number": "4242424242424241",
            "cvv": "123",
            "exp_month": "12",
            "exp_year": "2029",
            "holder_name": "DEMO USER",
            "billing_address": {
                "line1": "1 Main St",
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "US",
            },
            "available_balance": 50,
            "max_bind_count": 1,
        },
    )
    assert invalid.status_code == 400

    listing = client.get("/api/v1/admin/cards", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.json()["summary"]["total"] >= 1
    assert all("card_masked" in item for item in listing.json()["items"])

    monkeypatch.setattr(settings, "us_bin_allowlist", "999999")
    rejected_bin = client.post(
        "/api/v1/admin/cards",
        headers=admin_headers,
        json={
            "provider": "RDVCC",
            "card_number": "4242424242424242",
            "cvv": "123",
            "exp_month": "12",
            "exp_year": "2029",
            "holder_name": "DEMO USER",
            "billing_address": {
                "line1": "1 Main St",
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "US",
            },
            "available_balance": 50,
            "max_bind_count": 1,
        },
    )
    assert rejected_bin.status_code == 400


def test_products_and_tasks_endpoints(client, admin_headers):
    products = client.get("/api/v1/admin/products", headers=admin_headers)
    assert products.status_code == 200
    assert len(products.json()["items"]) >= 10

    tasks = client.get("/api/v1/admin/tasks", headers=admin_headers)
    assert tasks.status_code == 200
    assert "items" in tasks.json()

    channels = client.get("/api/v1/admin/channels", headers=admin_headers)
    assert channels.status_code == 200
    assert channels.json()["upstream"]["mode"] == "mock"


def test_admin_endpoints_require_auth(client):
    response = client.get("/api/v1/admin/overview")
    assert response.status_code == 401
