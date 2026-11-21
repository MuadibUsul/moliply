"""Catalogue serialization and stock helpers."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    AccountInventory,
    ActivationCode,
    AVAILABILITY_LABELS,
    CATEGORY_LABELS,
    ChannelMode,
    CodeStatus,
    DELIVERY_LABELS,
    FulfillmentStrategy,
    InventoryStatus,
    OrderItem,
    Product,
    ProductAvailability,
    ProductCategory,
    ProductVariant,
    SkuType,
    SKU_LABELS,
)


def money(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def available_sku_stock(db: Session, sku_type: SkuType | None, channel_mode: ChannelMode | None = None) -> int:
    """Unused, unbound, unsold codes for one concrete SKU/channel."""
    if sku_type is None:
        return 0
    query = db.query(func.count(ActivationCode.id)).filter(
        ActivationCode.sku_type == sku_type,
        ActivationCode.status == CodeStatus.UNUSED,
        ActivationCode.bound_account_email.is_(None),
        ActivationCode.order_item_id.is_(None),
    )
    if channel_mode is not None:
        query = query.filter(ActivationCode.channel_mode == channel_mode)
    return query.scalar() or 0


def available_stock(db: Session, product: Product) -> int:
    if product.delivery_type.value == "ACTIVATION_CODE":
        skus = [variant.sku_type for variant in product.variants if variant.sku_type]
        if not skus:
            return 0
        return (
            db.query(func.count(ActivationCode.id))
            .filter(
                ActivationCode.sku_type.in_(skus),
                ActivationCode.status == CodeStatus.UNUSED,
                ActivationCode.bound_account_email.is_(None),
                ActivationCode.order_item_id.is_(None),
            )
            .scalar()
            or 0
        )
    return (
        db.query(func.count(AccountInventory.id))
        .filter(
            AccountInventory.product_id == product.id,
            AccountInventory.status == InventoryStatus.AVAILABLE,
        )
        .scalar()
        or 0
    )


def available_account_stock(db: Session, product: Product) -> int:
    """Stock of a credential product: finished accounts, JIT free pool, or ready CCE profiles."""
    if product.fulfillment_strategy == FulfillmentStrategy.CCE_ACCOUNT_KEY:
        from app.services.fulfillment import cce_delivery

        return cce_delivery.available_cce_profiles()
    if product.fulfillment_strategy == FulfillmentStrategy.JIT_ACCOUNT_RECHARGE:
        from app.services.fulfillment import jit_delivery

        if jit_delivery.jit_enabled():
            from app.models import FreeAccountPool, FreeAccountStatus

            return (
                db.query(func.count(FreeAccountPool.id))
                .filter(
                    FreeAccountPool.product_id == product.id,
                    FreeAccountPool.status == FreeAccountStatus.AVAILABLE,
                )
                .scalar()
                or 0
            )
    return available_stock(db, product)


def secondary_market_ready() -> bool:
    """True when *something* can still deliver a product with an empty shelf.

    Either the automated upstream channel has credentials, or the operator
    handles it by hand. The manual desk is assumed available unless explicitly
    switched off, because a human reading the Feishu alert is the last resort.
    """
    from app.services import channel_dispatcher
    from app.services import runtime_settings as runtime

    if channel_dispatcher.upstream_configured():
        return True
    return runtime.get_bool("manual_backorder_enabled", True)


def product_availability(db: Session | None, product: Product) -> str:
    """What the buy button may do: sell now, sell on backorder, or restock.

    The distinction matters commercially. A full shelf sells normally; an empty
    shelf with a fallback channel stays purchasable (the customer gets a MOLI
    code and the order routes upstream/手动); only a truly stranded product is
    greyed out.
    """
    if db is None:
        return ProductAvailability.IN_STOCK.value
    stock = available_account_stock(db, product)
    if stock > 0:
        return ProductAvailability.IN_STOCK.value
    if secondary_market_ready():
        return ProductAvailability.BACKORDER.value
    return ProductAvailability.RESTOCKING.value


def purchasable(db: Session | None, product: Product) -> bool:
    if db is None:
        return True
    return product_availability(db, product) != ProductAvailability.RESTOCKING.value


def serialize_variant(variant: ProductVariant) -> dict:
    return {
        "id": variant.id,
        "label": variant.label,
        "months": variant.months,
        "price": money(variant.price),
        "original_price": money(variant.original_price) if variant.original_price else None,
        "sku_type": variant.sku_type.value if variant.sku_type else None,
        "sku_label": SKU_LABELS.get(variant.sku_type.value if variant.sku_type else "", ""),
        "channel_mode": variant.channel_mode.value,
        "active": variant.active,
    }


def serialize_product(db: Session | None, product: Product, include_stock: bool = True) -> dict:
    stock = available_account_stock(db, product) if (db is not None and include_stock) else None
    availability = (
        product_availability(db, product) if (db is not None and include_stock) else ProductAvailability.IN_STOCK.value
    )
    return {
        "id": product.id,
        "slug": product.slug,
        "name": product.name,
        "subtitle": product.subtitle,
        "category": product.category.value,
        "category_label": CATEGORY_LABELS.get(product.category.value, product.category.value),
        "brand": product.brand,
        "logo_key": product.logo_key,
        "delivery_type": product.delivery_type.value,
        "delivery_label": DELIVERY_LABELS.get(product.delivery_type.value, ""),
        "badge": product.badge,
        "tags": product.tags or [],
        "price": money(product.price),
        "original_price": money(product.original_price) if product.original_price else None,
        # Social proof is the offset plus the units actually sold; the raw
        # counter stays available for the admin console.
        "sold_count": (product.base_sales_count or 0) + (product.sold_count or 0),
        "real_sold_count": product.sold_count or 0,
        "base_sales_count": product.base_sales_count or 0,
        "warranty": product.warranty,
        "delivery_note": product.delivery_note,
        "description": product.description,
        "usage_steps": product.usage_steps or [],
        "features": product.features or [],
        "faq": product.faq or [],
        "active": product.active,
        "fulfillment_strategy": product.fulfillment_strategy.value,
        "variants": [serialize_variant(v) for v in product.variants if v.active],
        "stock": stock,
        "availability": availability,
        "availability_label": AVAILABILITY_LABELS.get(availability, ""),
        "purchasable": availability != ProductAvailability.RESTOCKING.value,
    }


def list_categories(db: Session) -> list[dict]:
    counts = dict(
        db.query(Product.category, func.count(Product.id))
        .filter(Product.active.is_(True))
        .group_by(Product.category)
        .all()
    )
    return [
        {
            "key": category.value,
            "label": CATEGORY_LABELS[category.value],
            "count": int(counts.get(category, 0)),
        }
        for category in ProductCategory
    ]


def variant_map(product: Product) -> dict[int, ProductVariant]:
    return {variant.id: variant for variant in product.variants}


def sku_label(sku_type: SkuType | None) -> str:
    return SKU_LABELS.get(sku_type.value if sku_type else "", "数字权益")


def channel_label(mode: ChannelMode | None) -> str:
    return "自营卡池" if mode == ChannelMode.SELF_VCC else "上游 API"


def order_item_sku(item: OrderItem) -> str:
    return sku_label(item.sku_type)
