from __future__ import annotations

from decimal import Decimal

from app.models import DeliveryType, Product, ProductCategory, ProductVariant, SkuType


def test_stock_is_enforced_per_variant_sku(client, db):
    product = Product(
        slug="test-empty-sku-product",
        name="Test Empty SKU",
        subtitle="test",
        category=ProductCategory.DIGITAL,
        brand="Test",
        logo_key="default",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price=Decimal("9.90"),
        active=True,
    )
    product.variants.append(
        ProductVariant(
            label="1 month",
            months=1,
            price=Decimal("9.90"),
            sku_type=SkuType.GENERIC_DIGITAL,
            active=True,
        )
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    variant = product.variants[0]
    try:
        response = client.post(
            "/api/v1/store/orders",
            json={
                "email": "stock@example.com",
                "password": "CustomerPass!123",
                "items": [{"product_id": product.id, "variant_id": variant.id, "quantity": 1}],
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "OUT_OF_STOCK"
    finally:
        db.delete(product)
        db.commit()
