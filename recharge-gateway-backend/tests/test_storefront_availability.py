"""Storefront social proof, backorder availability and trial-card seeding."""

from __future__ import annotations

from decimal import Decimal

from app.models import (
    ChannelMode,
    CodeStatus,
    DeliveryType,
    Product,
    ProductAvailability,
    ProductCategory,
    ProductVariant,
    SkuType,
)
from app.services import catalog


def _stock_product(db, slug="availability-probe", sku=SkuType.CODEX_SMS_US) -> Product:
    product = Product(
        slug=slug,
        name="Availability Probe",
        subtitle="probe",
        category=ProductCategory.DIGITAL,
        brand="Test",
        logo_key="default",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price=Decimal("9.90"),
        active=True,
    )
    product.variants.append(
        ProductVariant(label="1 unit", months=1, price=Decimal("9.90"), sku_type=sku, active=True)
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def _unused_code(db, sku=SkuType.CODEX_SMS_US) -> None:
    from app.models import ActivationCode
    from app.services.code_generator import generate_code

    db.add(
        ActivationCode(
            code=generate_code(),
            sku_type=sku,
            channel_mode=ChannelMode.UPSTREAM,
            status=CodeStatus.UNUSED,
        )
    )
    db.commit()


def test_sold_count_is_offset_plus_real_sales(db):
    product = Product(
        slug="sold-offset-probe",
        name="Sold Offset",
        subtitle="probe",
        category=ProductCategory.DIGITAL,
        brand="Test",
        logo_key="default",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price=Decimal("1.00"),
        sold_count=7,
        base_sales_count=300,
        active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    payload = catalog.serialize_product(db, product)
    assert payload["sold_count"] == 307
    assert payload["real_sold_count"] == 7
    assert payload["base_sales_count"] == 300


def test_zero_base_offset_still_reports_real_sales(db):
    product = _stock_product(db, slug="sold-zero-probe")
    payload = catalog.serialize_product(db, product)
    assert payload["sold_count"] == 0


def test_empty_shelf_is_backorder_when_upstream_is_configured(db, monkeypatch):
    product = _stock_product(db, slug="backorder-probe")
    monkeypatch.setattr(catalog, "secondary_market_ready", lambda: True)

    payload = catalog.serialize_product(db, product)
    assert payload["availability"] == ProductAvailability.BACKORDER.value
    assert payload["purchasable"] is True
    assert "专属通道保障" in payload["availability_label"]


def test_empty_shelf_restocks_when_nothing_can_serve_it(db, monkeypatch):
    product = _stock_product(db, slug="restocking-probe")
    monkeypatch.setattr(catalog, "secondary_market_ready", lambda: False)

    payload = catalog.serialize_product(db, product)
    assert payload["availability"] == ProductAvailability.RESTOCKING.value
    assert payload["purchasable"] is False


def test_stocked_product_is_in_stock(db, monkeypatch):
    product = _stock_product(db, slug="in-stock-probe")
    monkeypatch.setattr(catalog, "secondary_market_ready", lambda: False)
    _unused_code(db)

    payload = catalog.serialize_product(db, product)
    assert payload["availability"] == ProductAvailability.IN_STOCK.value
    assert payload["purchasable"] is True


def test_backorder_order_is_accepted_and_flagged_stock_pending(client, monkeypatch):
    from app.services import catalog as catalog_module

    monkeypatch.setattr(catalog_module, "secondary_market_ready", lambda: True)
    product = _stock_product(db=None, slug="") if False else None  # readability guard
    assert product is None

    with __import__("app.database", fromlist=["SessionLocal"]).SessionLocal() as db_session:
        product = _stock_product(db_session, slug="backorder-order-probe", sku=SkuType.CODEX_SMS_US)
        variant_id = product.variants[0].id
        product_id = product.id

    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": "backorder@example.com",
            "password": "CustomerPass!123",
            "items": [{"product_id": product_id, "variant_id": variant_id, "quantity": 1}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["order"]["items"][0]["sku_type"] == SkuType.CODEX_SMS_US.value


def test_seeded_trial_card_exists_and_is_cheap(client):
    response = client.get("/api/v1/store/products/ai-icebreaker-trial")
    assert response.status_code == 200, response.text
    product = response.json()["product"]
    assert product["price"] == 3.90
    assert product["base_sales_count"] > 0
    assert product["availability_label"]


def test_seeded_flagship_products_carry_an_offset(client):
    for slug in ("chatgpt-plus-1m-recharge", "claude-pro-1m-recharge", "cursor-pro"):
        product = client.get(f"/api/v1/store/products/{slug}").json()["product"]
        assert product["base_sales_count"] >= 60, slug
        assert product["sold_count"] == product["base_sales_count"] + product["real_sold_count"]


def test_live_telemetry_reports_shape(client):
    response = client.get("/api/v1/store/live-telemetry")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["gateway_status"] in {"NORMAL", "BUSY", "DEGRADED"}
    assert 0 <= payload["uptime_percent"] <= 100
    assert payload["avg_seconds"] >= 1
    assert isinstance(payload["recent"], list)


def test_live_telemetry_masks_emails(client, db):
    from app.models import ActivationCode, RechargeState, RechargeTask, utcnow
    from app.services.code_generator import generate_code
    from app.services.crypto import encrypt_text

    code = ActivationCode(
        code=generate_code(),
        sku_type=SkuType.GENERIC_DIGITAL,
        channel_mode=ChannelMode.UPSTREAM,
        status=CodeStatus.USED,
    )
    db.add(code)
    db.flush()
    db.add(
        RechargeTask(
            task_id="telemetry-probe-1",
            code_id=code.id,
            target_email="someone@example.com",
            encrypted_access_token=encrypt_text("token"),
            state=RechargeState.SUCCESS,
            completed_at=utcnow(),
            created_at=utcnow(),
        )
    )
    db.commit()

    payload = client.get("/api/v1/store/live-telemetry").json()
    assert payload["recent"][0]["email"] == "s***@example.com"
