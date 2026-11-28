from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.main import _SecretRedactingFilter
from app.models import (
    ActivationCode,
    CodeStatus,
    Coupon,
    DeliveryType,
    Order,
    OrderDelivery,
    OrderItem,
    OrderStatus,
    Product,
    SkuType,
    utcnow,
)
from app.services import automation
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.fulfillment import cce_delivery


def test_refund_and_revoke_disables_codes_and_revokes_cce_key(client, db, admin_headers, monkeypatch):
    """Refunding an order via /admin/orders/{order_no}/refund-and-revoke disables codes and revokes CCE tunnel keys."""
    revoked_payloads: list[str] = []

    def fake_revoke(payload_text: str) -> bool:
        revoked_payloads.append(payload_text)
        return True

    monkeypatch.setattr(cce_delivery, "revoke_delivery_payload", fake_revoke)

    product = db.query(Product).first()
    assert product is not None

    code_val = generate_code()
    code = ActivationCode(code=code_val, sku_type=SkuType.CHATGPT_PLUS_1M, status=CodeStatus.UNUSED)
    db.add(code)
    db.flush()

    order = Order(
        order_no="MOL20261128999999",
        email="refund.buyer@example.com",
        amount=Decimal("149.00"),
        status=OrderStatus.DELIVERED,
        paid_at=utcnow(),
        delivered_at=utcnow(),
    )
    db.add(order)
    db.flush()

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        product_name=product.name,
        spec_label="1m",
        unit_price=Decimal("149.00"),
        quantity=1,
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
    )
    db.add(item)
    db.flush()

    db.add(
        OrderDelivery(
            order_item_id=item.id,
            kind="ACTIVATION_CODE",
            code_id=code.id,
            payload_enc=encrypt_text(code_val),
        )
    )
    db.add(
        OrderDelivery(
            order_item_id=item.id,
            kind="CCE_DELIVERY_KEY",
            payload_enc=encrypt_text("CCE_KEY=cce_revoke_me_123\nTUNNEL_SECRET=sec_abc"),
        )
    )
    db.commit()

    resp = client.post(
        f"/api/v1/admin/orders/{order.order_no}/refund-and-revoke?reason=chargeback",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "REFUNDED"
    assert data["disabled_codes"] == 1
    assert data["revoked_cce_keys"] == 1
    assert len(revoked_payloads) == 1
    assert "cce_revoke_me_123" in revoked_payloads[0]

    db.refresh(code)
    assert code.status == CodeStatus.DISABLED


def test_27_day_renewal_reminders_mint_single_use_coupon_once(db):
    """27-day renewal job creates a 48h single-use comeback coupon and is idempotent on re-run."""
    delivered_27d_ago = utcnow() - timedelta(days=27, hours=6)
    order = Order(
        order_no="MOL20261101888888",
        email="loyal.buyer@example.com",
        amount=Decimal("149.00"),
        status=OrderStatus.DELIVERED,
        paid_at=delivered_27d_ago,
        delivered_at=delivered_27d_ago,
    )
    db.add(order)
    db.commit()

    first = automation.send_renewal_reminders(db)
    assert first["reminded"] >= 1
    assert first["coupons_created"] >= 1

    second = automation.send_renewal_reminders(db)
    assert second["reminded"] == 0

    renew_coupon = db.query(Coupon).filter(Coupon.code.like("RENEW%")).first()
    assert renew_coupon is not None
    assert renew_coupon.max_uses == 1
    assert renew_coupon.amount == Decimal("5.00")


def test_secret_redacting_log_filter_masks_pans_and_tokens():
    raw = "Failed card 4111-2222-3333-4444 with Authorization: Bearer superSecretToken12345 and sk-ant-sid01-abcdef123456"
    sanitized = _SecretRedactingFilter.sanitize(raw)
    assert "4111" not in sanitized
    assert "superSecretToken12345" not in sanitized
    assert "sk-ant-sid01" not in sanitized
    assert "[REDACTED_PAN]" in sanitized
    assert "[REDACTED_TOKEN]" in sanitized
    assert "[REDACTED_CLAUDE_KEY]" in sanitized


def test_order_frequency_limits_and_payment_ip_match(db):
    """Large orders (>=200) limited to 1/24h, small orders (<200) limited to 1/30m, and payment IP must match order IP."""
    from types import SimpleNamespace
    import pytest
    from app.services import orders as order_service
    from app.services import runtime_settings

    runtime_settings.update_settings(
        db,
        {
            "order_risk_limits_enabled": "true",
            "large_order_threshold_cny": "200",
            "large_order_window_hours": "24",
            "small_order_cooldown_minutes": "30",
            "enforce_payment_ip_match": "true",
            "support_wechat": "MOLIPLY-VIP",
        },
        actor="test",
    )

    small_prod = db.query(Product).filter(Product.price < Decimal("200"), Product.active.is_(True)).first()
    assert small_prod is not None

    # 1. Small order: first succeeds, second within 30 min from same email/IP fails with 429 + support guidance
    first_small = order_service.create_order(
        db,
        email="small.limit@example.com",
        items=[SimpleNamespace(product_id=small_prod.id, variant_id=None, quantity=1)],
        coupon_code=None,
        client_ip="203.0.113.10",
    )
    assert first_small.order_no.startswith("MOL")

    with pytest.raises(order_service.OrderError) as exc_small:
        order_service.create_order(
            db,
            email="small.limit@example.com",
            items=[SimpleNamespace(product_id=small_prod.id, variant_id=None, quantity=1)],
            coupon_code=None,
            client_ip="203.0.113.10",
        )
    assert exc_small.value.code == "SMALL_ORDER_RATE_LIMIT"
    assert exc_small.value.status_code == 429
    assert "MOLIPLY-VIP" in exc_small.value.message

    # 2. Payment IP mismatch is blocked with 403; matching IP succeeds
    with pytest.raises(order_service.OrderError) as exc_ip:
        order_service.create_payment(db, first_small, "wechat", "198.51.100.99")
    assert exc_ip.value.code == "PAYMENT_IP_MISMATCH"
    assert exc_ip.value.status_code == 403
    assert "MOLIPLY-VIP" in exc_ip.value.message

    intent = order_service.create_payment(db, first_small, "wechat", "203.0.113.10")
    assert intent["order_no"] == first_small.order_no

    # 3. Large order (>= 200): first succeeds, second within 24h from same IP (even different email) fails with 429
    large_qty = max(1, int((Decimal("200") // max(Decimal("1"), small_prod.price)) + 1))
    first_large = order_service.create_order(
        db,
        email="large.limit1@example.com",
        items=[SimpleNamespace(product_id=small_prod.id, variant_id=None, quantity=large_qty)],
        coupon_code=None,
        client_ip="203.0.113.20",
    )
    assert first_large.amount >= Decimal("200")

    with pytest.raises(order_service.OrderError) as exc_large:
        order_service.create_order(
            db,
            email="large.limit2@example.com",
            items=[SimpleNamespace(product_id=small_prod.id, variant_id=None, quantity=large_qty)],
            coupon_code=None,
            client_ip="203.0.113.20",
        )
    assert exc_large.value.code == "LARGE_ORDER_DAILY_LIMIT"
    assert exc_large.value.status_code == 429
    assert "MOLIPLY-VIP" in exc_large.value.message


def test_pay_first_lock_after_payment_invariant(db):
    """Unpaid PENDING orders must never lock inventory or generate deliveries until payment is settled."""
    from types import SimpleNamespace
    from app.models import AccountInventory, InventoryStatus
    from app.services import orders as order_service

    acct_prod = (
        db.query(Product)
        .filter(Product.delivery_type == DeliveryType.ACCOUNT_CREDENTIALS, Product.active.is_(True))
        .first()
    )
    assert acct_prod is not None

    before_avail = (
        db.query(AccountInventory)
        .filter(AccountInventory.product_id == acct_prod.id, AccountInventory.status == InventoryStatus.AVAILABLE)
        .count()
    )
    order = order_service.create_order(
        db,
        email="payfirst@example.com",
        items=[SimpleNamespace(product_id=acct_prod.id, variant_id=None, quantity=1)],
        coupon_code=None,
        client_ip="203.0.113.55",
    )
    assert order.status == OrderStatus.PENDING
    assert sum(len(item.deliveries) for item in order.items) == 0

    after_create_avail = (
        db.query(AccountInventory)
        .filter(AccountInventory.product_id == acct_prod.id, AccountInventory.status == InventoryStatus.AVAILABLE)
        .count()
    )
    # Zero inventory locked before payment!
    assert after_create_avail == before_avail
