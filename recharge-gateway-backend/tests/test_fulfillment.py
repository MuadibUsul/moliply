from __future__ import annotations

from app.config import settings
from app.models import AccountInventory, InventoryStatus, Order, OrderStatus
from app.services.crypto import encrypt_text
from app.services.orders import fulfill_order


def _product(client, slug):
    return client.get(f"/api/v1/store/products/{slug}").json()["product"]


def _order(client, slug, email):
    product = _product(client, slug)
    variant = product["variants"][0]
    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": email,
            "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 1}],
        },
    )
    assert response.status_code == 201, response.text
    return product, response.json()["order"]


def test_fulfillment_is_idempotent(client, db):
    _, order = _order(client, "chatgpt-plus-1m-recharge", "idem@example.com")
    client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle")
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    assert row.status == OrderStatus.DELIVERED
    first_count = len(row.items[0].deliveries)
    fulfill_order(db, row)
    db.refresh(row)
    assert len(row.items[0].deliveries) == first_count


def test_paid_order_with_no_stock_waits_for_restock(client, db, monkeypatch):
    product, order = _order(client, "notion-plus", "stock@example.com")
    accounts = (
        db.query(AccountInventory)
        .filter(
            AccountInventory.product_id == product["id"],
            AccountInventory.status == InventoryStatus.AVAILABLE,
        )
        .all()
    )
    for account in accounts:
        account.status = InventoryStatus.DISABLED
        db.add(account)
    db.commit()

    monkeypatch.setattr(settings, "enforce_stock", False)
    settled = client.post(f"/api/v1/store/orders/{order['order_no']}/mock-settle")
    assert settled.status_code == 200
    body = settled.json()["order"]
    assert body["status"] == "PAID"
    assert body["stock_pending"] is True
    assert body["items"][0]["deliveries"] == []

    db.add(
        AccountInventory(
            product_id=product["id"],
            label="restocked",
            payload_enc=encrypt_text("restock@example.com----pass----backup@example.com----2026-12-01"),
            status=InventoryStatus.AVAILABLE,
        )
    )
    db.commit()

    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    fulfill_order(db, row)
    db.refresh(row)
    assert row.status == OrderStatus.DELIVERED
    assert row.stock_pending is False
    assert len(row.items[0].deliveries) == 1
