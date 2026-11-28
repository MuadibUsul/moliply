"""Storefront order, payment and auto-delivery workflow."""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import (
    AccountInventory,
    ActivationCode,
    AuditLog,
    ChannelMode,
    CodeStatus,
    Coupon,
    DeliveryType,
    FulfillmentStrategy,
    InventoryStatus,
    Order,
    OrderDelivery,
    OrderItem,
    OrderStatus,
    PaymentEvent,
    Product,
    ProductVariant,
    utcnow,
)
from app.services import feishu
from app.services import runtime_settings as runtime
from app.services.fulfillment import jit_delivery
from app.services.catalog import available_account_stock, available_sku_stock, available_stock, money
from app.services.crypto import decrypt_text, encrypt_text, mask_email
from app.services.mailer import send_delivery_email
from app.services.payments import get_callback_provider, get_provider, masked_order_title
from app.services.payments.base import PaymentProviderError
from app.services.payments.guards import mock_payments_allowed
from app.services.payments.trc20 import DirectTrc20Provider
from app.services.payments.direct_multi import DirectBitcoinProvider, DirectEthereumProvider
from app.services import direct_crypto

logger = logging.getLogger(__name__)

ORDER_STATUS_LABELS = {
    "PENDING": "等待支付",
    "PAID": "已支付 · 备货中",
    "DELIVERED": "已交付",
    "FAILED": "支付失败",
    "CANCELLED": "已取消",
    "REFUNDED": "已退款",
}

PAYMENT_LABELS = {
    "wechat": "微信支付",
    "alipay": "支付宝",
    "wechat_native": "微信支付",
    "alipay_f2f": "支付宝",
    "crypto": "加密货币",
    "nowpayments": "加密货币",
    "usdt_trc20": "USDT-TRC20",
    "usdc_erc20": "USDC-ERC20",
    "eth": "ETH",
    "btc": "BTC",
    "mock": "模拟支付",
    "mock_wechat": "微信支付",
    "mock_alipay": "支付宝",
}


class OrderError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def generate_order_no(db: Session) -> str:
    prefix = "MOL" + utcnow().strftime("%Y%m%d")
    for _ in range(12):
        candidate = f"{prefix}{secrets.randbelow(900000) + 100000}"
        if not db.query(Order.id).filter(Order.order_no == candidate).first():
            return candidate
    raise OrderError("ORDER_NO_FAILED", "订单号生成失败，请重试", 500)


def validate_coupon(
    db: Session,
    code: str | None,
    amount: Decimal,
    *,
    lock_row: bool = False,
) -> tuple[Coupon | None, Decimal]:
    if not code:
        return None, Decimal("0")
    query = db.query(Coupon).filter(func.upper(Coupon.code) == code.strip().upper())
    if lock_row:
        query = query.with_for_update()
    coupon = query.first()
    if not coupon or not coupon.active:
        raise OrderError("COUPON_INVALID", "优惠码无效")
    now = utcnow()
    if coupon.expires_at and coupon.expires_at < now:
        raise OrderError("COUPON_EXPIRED", "优惠码已过期")
    if coupon.max_uses is not None:
        # Include active unexpired PENDING orders so one buyer cannot reserve
        # N parallel orders against a max_uses=1 coupon and pay all of them.
        pending_uses = int(
            db.query(func.count(Order.id))
            .filter(
                Order.status == OrderStatus.PENDING,
                func.upper(Order.coupon_code) == coupon.code.upper(),
                (Order.expires_at.is_(None)) | (Order.expires_at > now),
            )
            .scalar()
            or 0
        )
        if coupon.used_count + pending_uses >= coupon.max_uses:
            raise OrderError("COUPON_EXHAUSTED", "优惠码已被领完")
    if amount < coupon.min_amount:
        raise OrderError("COUPON_THRESHOLD", f"订单满 ¥{coupon.min_amount:.0f} 可使用该优惠码")
    return coupon, min(coupon.amount, amount)


def create_order(
    db: Session,
    *,
    email: str,
    items: list[Any],
    coupon_code: str | None,
    client_ip: str,
    user_id: int | None = None,
) -> Order:
    from contextlib import nullcontext
    from app.services.locks import redis_lock

    normalized_email = str(email).strip().lower()
    now = utcnow()
    max_pending_email = runtime.get_int("max_pending_orders_per_email", 10 if settings.is_production else 0)
    if max_pending_email > 0:
        active_pending = int(
            db.query(func.count(Order.id))
            .filter(
                Order.email == normalized_email,
                Order.status == OrderStatus.PENDING,
                (Order.expires_at.is_(None)) | (Order.expires_at > now),
            )
            .scalar()
            or 0
        )
        if active_pending >= max_pending_email:
            raise OrderError(
                "TOO_MANY_PENDING_ORDERS",
                "您当前有过多未支付订单，请先完成支付或等待超时后再下单",
                429,
            )

    lock_ctx = (
        redis_lock(f"lock:coupon:{coupon_code.strip().upper()}", ttl_seconds=10)
        if coupon_code and coupon_code.strip()
        else nullcontext(True)
    )
    with lock_ctx as acquired:
        if not acquired:
            raise OrderError("COUPON_BUSY", "优惠码核验繁忙，请稍后重试", 429)
        return _create_order_locked(
            db,
            email=normalized_email,
            items=items,
            coupon_code=coupon_code,
            client_ip=client_ip,
            user_id=user_id,
        )


def _create_order_locked(
    db: Session,
    *,
    email: str,
    items: list[Any],
    coupon_code: str | None,
    client_ip: str,
    user_id: int | None = None,
) -> Order:
    order = Order(
        order_no=generate_order_no(db),
        user_id=user_id,
        email=email,
        amount=Decimal("0"),
        status=OrderStatus.PENDING,
        client_ip=client_ip,
        expires_at=utcnow() + timedelta(minutes=runtime.get_int("order_expire_minutes", settings.order_expire_minutes)),
    )
    db.add(order)
    db.flush()

    subtotal = Decimal("0")
    for requested in items:
        product = db.get(Product, requested.product_id)
        if not product or not product.active:
            raise OrderError("PRODUCT_NOT_FOUND", "商品已下架，请刷新后重试", 404)
        active_variants = [variant for variant in product.variants if variant.active]
        variant = None
        if requested.variant_id is not None:
            variant = next((candidate for candidate in active_variants if candidate.id == requested.variant_id), None)
            if variant is None:
                raise OrderError("VARIANT_NOT_FOUND", "商品规格不存在，请刷新后重试", 404)
        elif active_variants:
            variant = active_variants[0]

        unit_price = variant.price if variant else product.price
        sku_type = variant.sku_type if variant else (active_variants[0].sku_type if active_variants else None)
        channel_mode = variant.channel_mode if variant else ChannelMode.UPSTREAM
        if product.delivery_type == DeliveryType.ACTIVATION_CODE and not sku_type:
            raise OrderError("SKU_NOT_CONFIGURED", "该商品暂未配置激活码规格，请联系客服", 409)

        if runtime.get_bool("enforce_stock", settings.enforce_stock):
            stock = (
                available_sku_stock(db, sku_type)
                if product.delivery_type == DeliveryType.ACTIVATION_CODE
                else available_account_stock(db, product)
            )
            if stock < requested.quantity:
                from app.services.catalog import secondary_market_ready

                # An empty shelf only blocks the order when nothing can serve it.
                # With an upstream channel or the manual desk behind the shop the
                # item stays sellable and lands in ``stock_pending``.
                if not secondary_market_ready():
                    raise OrderError(
                        "OUT_OF_STOCK",
                        f"{product.name} 暂时缺货，请选择其他商品或稍后再试",
                        409,
                    )

        line_total = unit_price * requested.quantity
        subtotal += line_total
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id if variant else None,
                product_name=product.name,
                spec_label=variant.label if variant else "",
                logo_key=product.logo_key,
                brand=product.brand,
                unit_price=unit_price,
                quantity=requested.quantity,
                delivery_type=product.delivery_type,
                sku_type=sku_type,
                channel_mode=channel_mode,
            )
        )

    coupon, discount = validate_coupon(db, coupon_code, subtotal, lock_row=True)
    order.amount = max(Decimal("0.01"), subtotal - discount)
    order.discount_amount = discount
    order.coupon_code = coupon.code if coupon else None
    db.add(order)
    db.add(
        AuditLog(
            actor="customer",
            action="order.create",
            entity_type="order",
            entity_id=order.order_no,
            detail=f"amount={order.amount}",
            ip=client_ip,
        )
    )
    db.commit()
    db.refresh(order)
    feishu.dispatch_event(
        "order.created",
        "MOLIPLY 新订单",
        {
            "订单号": order.order_no,
            "金额": f"¥{order.amount:.2f}",
            "邮箱": mask_email(order.email),
            "商品": "、".join(item.product_name for item in order.items),
        },
        severity="info",
        link_path=f"/admin/orders?q={order.order_no}",
    )
    return order


def _direct_payment_response(order: Order) -> dict[str, Any]:
    remaining = 0
    if order.expires_at:
        remaining = max(0, int((order.expires_at - utcnow()).total_seconds()))
    return {
        "provider": order.payment_provider or "usdt_trc20",
        "qr_content": order.payment_address or "",
        "pay_url": "",
        "trade_no": "",
        "mock": False,
        "expires_in": remaining,
        "order_no": order.order_no,
        "amount": money(order.amount),
        "payment_address": order.payment_address or "",
        "payment_amount": money(order.payment_amount),
        "payment_currency": order.payment_currency or "USDT",
        "network": order.payment_network or "",
        "expires_at": order.expires_at.isoformat() + "Z" if order.expires_at else "",
    }


def _direct_provider_for(coin: str):
    if coin == "USDT_TRC20":
        return DirectTrc20Provider()
    if coin in {"USDC_ERC20", "ETH"}:
        return DirectEthereumProvider(coin)
    if coin == "BTC":
        return DirectBitcoinProvider()
    raise PaymentProviderError("CRYPTO_COIN_UNSUPPORTED", "不支持的加密货币币种")


def create_payment(db: Session, order: Order, provider_name: str, client_ip: str, coin: str = ""):
    from app.services.locks import redis_lock

    order = (
        db.query(Order)
        .filter(Order.id == order.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)
    if order.status == OrderStatus.DELIVERED:
        raise OrderError("ORDER_ALREADY_PAID", "订单已支付并交付")
    if order.status != OrderStatus.PENDING:
        raise OrderError("ORDER_NOT_PENDING", "当前订单状态不可支付")
    if order.expires_at and order.expires_at < utcnow():
        order.status = OrderStatus.CANCELLED
        db.add(order)
        db.commit()
        raise OrderError("ORDER_EXPIRED", "订单已超时，请重新下单")

    direct_mode = (
        provider_name in {"crypto", "usdt_trc20", "usdc_erc20", "eth", "btc"}
        and runtime.get("crypto_provider", settings.crypto_provider).lower() == "usdt_trc20"
    )
    if direct_mode:
        enabled = direct_crypto.enabled_coins()
        if not enabled:
            raise OrderError("CRYPTO_NOT_CONFIGURED", "自建加密收款尚未配置", 502)
        requested = (coin or "").strip().upper()
        if requested and requested not in enabled:
            raise OrderError("CRYPTO_COIN_DISABLED", "该币种暂未启用", 409)
        selected = requested or enabled[0]
        if (
            order.payment_provider == selected.lower()
            and order.payment_address
            and order.payment_amount
            and order.expires_at
            and order.expires_at > utcnow()
        ):
            return _direct_payment_response(order)
        try:
            provider = _direct_provider_for(selected)
            with redis_lock(f"lock:crypto-amount:{selected.lower()}", ttl_seconds=10):
                payment_amount = provider.select_unique_amount(db, order.amount)
                intent = provider.create_native(
                    order.order_no,
                    order.amount,
                    masked_order_title(order.order_no),
                    client_ip,
                    payment_amount=payment_amount,
                )
                order.payment_provider = intent.provider
                order.payment_address = intent.payment_address
                order.payment_amount = intent.payment_amount
                order.payment_currency = intent.payment_currency
                order.payment_network = intent.network
                order.expires_at = utcnow() + timedelta(seconds=intent.expires_in)
                db.add(order)
                db.commit()
        except PaymentProviderError as exc:
            raise OrderError(exc.code, exc.message, 502) from exc
        return {
            "provider": intent.provider,
            "qr_content": intent.qr_content,
            "pay_url": intent.pay_url,
            "trade_no": intent.trade_no,
            "mock": intent.mock,
            "expires_in": intent.expires_in,
            "order_no": order.order_no,
            "amount": money(order.amount),
            "payment_address": intent.payment_address,
            "payment_amount": money(intent.payment_amount),
            "payment_currency": intent.payment_currency,
            "network": intent.network,
            "expires_at": intent.expires_at,
        }
    else:
        try:
            provider = get_provider(provider_name)
            intent = provider.create_native(
                order.order_no,
                order.amount,
                masked_order_title(order.order_no),
                client_ip,
            )
        except PaymentProviderError as exc:
            raise OrderError(exc.code, exc.message, 502) from exc

    order.payment_provider = intent.provider
    raw_amount = intent.raw.get("price_amount")
    if intent.provider in {"usdt_trc20", "usdc_erc20", "eth", "btc"}:
        order.payment_address = intent.payment_address
        order.payment_amount = intent.payment_amount
        order.payment_currency = intent.payment_currency
        order.payment_network = intent.network
        order.expires_at = utcnow() + timedelta(seconds=intent.expires_in)
    elif intent.provider == "crypto" and raw_amount is not None:
        order.payment_address = None
        order.payment_network = None
        order.payment_amount = Decimal(str(raw_amount))
        order.payment_currency = str(intent.raw.get("price_currency") or "USD").upper()
    elif intent.provider == "crypto":
        order.payment_address = None
        order.payment_network = None
        order.payment_amount = None
        order.payment_currency = None
    else:
        order.payment_address = None
        order.payment_network = None
        order.payment_amount = order.amount
        order.payment_currency = "CNY"
    db.add(order)
    db.commit()
    return {
        "provider": intent.provider,
        "qr_content": intent.qr_content,
        "pay_url": intent.pay_url,
        "trade_no": intent.trade_no,
        "mock": intent.mock,
        "expires_in": intent.expires_in,
        "order_no": order.order_no,
        "amount": money(order.amount),
        "payment_address": intent.payment_address,
        "payment_amount": money(intent.payment_amount),
        "payment_currency": intent.payment_currency,
        "network": intent.network,
        "expires_at": intent.expires_at,
    }


def _delivery_code_status(db: Session, code_ids: list[int]) -> dict[int, ActivationCode]:
    if not code_ids:
        return {}
    rows = db.query(ActivationCode).filter(ActivationCode.id.in_(code_ids)).all()
    return {row.id: row for row in rows}


def serialize_order(
    db: Session,
    order: Order,
    *,
    reveal: bool | None = None,
    reveal_email: bool = False,
) -> dict:
    if reveal is None:
        reveal = order.status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    code_ids = [
        delivery.code_id
        for item in order.items
        for delivery in item.deliveries
        if delivery.code_id
    ]
    codes = _delivery_code_status(db, code_ids)

    items = []
    for item in order.items:
        deliveries = []
        for delivery in item.deliveries:
            payload = decrypt_text(delivery.payload_enc)
            code = codes.get(delivery.code_id) if delivery.code_id else None
            deliveries.append(
                {
                    "id": delivery.id,
                    "kind": delivery.kind,
                    "payload": payload if reveal else None,
                    "payload_hint": payload[:4] + "••••" + payload[-4:] if (reveal and len(payload) > 12) else None,
                    "code_status": code.status.value if code else None,
                    "redeemed_at": code.used_at.isoformat() + "Z" if code and code.used_at else None,
                    "created_at": delivery.created_at.isoformat() + "Z",
                }
            )
        items.append(
            {
                "id": item.id,
                "product_id": item.product_id,
                "variant_id": item.variant_id,
                "product_name": item.product_name,
                "spec_label": item.spec_label,
                "logo_key": item.logo_key,
                "brand": item.brand,
                "unit_price": money(item.unit_price),
                "quantity": item.quantity,
                "delivery_type": item.delivery_type.value,
                "sku_type": item.sku_type.value if item.sku_type else None,
                "deliveries": deliveries,
                "delivered": len(deliveries) >= item.quantity,
            }
        )

    redeemed_times = [codes[d.code_id].used_at for item in order.items for d in item.deliveries if d.code_id in codes and codes[d.code_id].used_at]
    timeline = [
        {
            "key": "created",
            "label": "订单创建",
            "at": order.created_at.isoformat() + "Z",
            "done": True,
        },
        {
            "key": "paid",
            "label": "支付成功",
            "at": order.paid_at.isoformat() + "Z" if order.paid_at else None,
            "done": order.paid_at is not None,
        },
        {
            "key": "delivered",
            "label": "卡密发放",
            "at": order.delivered_at.isoformat() + "Z" if order.delivered_at else None,
            "done": order.status == OrderStatus.DELIVERED,
        },
        {
            "key": "redeemed",
            "label": "核销完成",
            "at": max(redeemed_times).isoformat() + "Z" if redeemed_times else None,
            "done": bool(redeemed_times),
        },
    ]

    return {
        "order_no": order.order_no,
        "email": order.email if reveal_email else mask_email(order.email),
        "amount": money(order.amount),
        "discount_amount": money(order.discount_amount),
        "coupon_code": order.coupon_code,
        "status": order.status.value,
        "status_label": ORDER_STATUS_LABELS.get(order.status.value, order.status.value),
        "payment_provider": order.payment_provider,
        "payment_label": PAYMENT_LABELS.get(order.payment_provider or "", order.payment_provider or ""),
        "payment_address": order.payment_address,
        "payment_amount": money(order.payment_amount) if order.payment_amount is not None else None,
        "payment_currency": order.payment_currency,
        "payment_network": order.payment_network,
        "stock_pending": order.stock_pending,
        "created_at": order.created_at.isoformat() + "Z",
        "expires_at": order.expires_at.isoformat() + "Z" if order.expires_at else None,
        "paid_at": order.paid_at.isoformat() + "Z" if order.paid_at else None,
        "delivered_at": order.delivered_at.isoformat() + "Z" if order.delivered_at else None,
        "items": items,
        "timeline": timeline,
    }


def find_user_orders(db: Session, user_id: int) -> list[Order]:
    return (
        db.query(Order)
        .filter(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )


def mark_order_paid(db: Session, order: Order, provider: str, trade_no: str) -> bool:
    # Serialise concurrent callbacks (and manual settlement) on the order row.
    order = (
        db.query(Order)
        .filter(Order.id == order.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)
    if order.status in {OrderStatus.PAID, OrderStatus.DELIVERED}:
        return False
    late_recovery = order.status == OrderStatus.CANCELLED
    if order.status not in {OrderStatus.PENDING, OrderStatus.CANCELLED}:
        raise OrderError("ORDER_NOT_PAYABLE", "当前订单状态不可完成支付")
    order.status = OrderStatus.PAID
    order.paid_at = utcnow()
    order.payment_provider = provider
    order.payment_trade_no = trade_no
    if order.coupon_code:
        coupon = (
            db.query(Coupon)
            .filter(Coupon.code == order.coupon_code)
            .with_for_update()
            .first()
        )
        if coupon:
            coupon.used_count += 1
            db.add(coupon)
    for item in order.items:
        db.query(Product).filter(Product.id == item.product_id).update(
            {Product.sold_count: Product.sold_count + item.quantity}
        )
    db.add(order)
    db.add(
        AuditLog(
            actor="payment",
            action="order.late_payment_recovered" if late_recovery else "order.paid",
            entity_type="order",
            entity_id=order.order_no,
            detail=f"provider={provider}" + (" late_after_cancel=true" if late_recovery else ""),
        )
    )
    db.commit()
    db.refresh(order)
    fulfill_order(db, order)
    if late_recovery:
        feishu.dispatch_event(
            "order.paid",
            "MOLIPLY 超时取消订单收到真实支付（已自动恢复发货）",
            {
                "订单号": order.order_no,
                "金额": f"¥{order.amount:.2f}",
                "支付通道": PAYMENT_LABELS.get(provider, provider),
                "邮箱": mask_email(order.email),
            },
            severity="warning",
            link_path=f"/admin/orders?q={order.order_no}",
        )
    else:
        feishu.dispatch_event(
            "order.paid",
            "MOLIPLY 支付成功",
            {
                "订单号": order.order_no,
                "金额": f"¥{order.amount:.2f}",
                "支付通道": PAYMENT_LABELS.get(provider, provider),
                "邮箱": mask_email(order.email),
            },
            severity="success",
            link_path=f"/admin/orders?q={order.order_no}",
        )
    if order.status == OrderStatus.DELIVERED:
        _send_delivery_notification_async(db, order)
    return True


def _send_delivery_notification_async(db: Session, order: Order) -> None:
    if not smtp_configured_safe():
        return
    if settings.task_mode.lower() == "celery":
        try:
            from app.tasks.celery_worker import send_delivery_email_task

            send_delivery_email_task.delay(order.order_no)
            return
        except Exception:  # noqa: BLE001
            logger.warning("Celery email enqueue failed for %s; falling back to thread", order.order_no)
    try:
        payload = serialize_order(db, order, reveal=True, reveal_email=True)
    except Exception:
        return
    threading.Thread(
        target=send_delivery_email,
        args=(order.email, payload),
        daemon=True,
    ).start()


def smtp_configured_safe() -> bool:
    from app.services.mailer import smtp_configured

    return smtp_configured()


def fulfill_order(db: Session, order: Order) -> Order:
    order = (
        db.query(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.deliveries))
        .filter(Order.id == order.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)
    if order.status == OrderStatus.PENDING:
        raise OrderError("ORDER_NOT_PAID", "订单尚未支付")
    if order.status not in {OrderStatus.PAID, OrderStatus.DELIVERED}:
        raise OrderError("ORDER_NOT_FULFILLABLE", "当前订单状态不可发货")

    was_delivered = order.status == OrderStatus.DELIVERED
    was_stock_pending = order.stock_pending
    pending = False
    jit_items: list[OrderItem] = []
    for item in order.items:
        missing = item.quantity - len(item.deliveries)
        if missing <= 0:
            continue
        if item.delivery_type == DeliveryType.ACTIVATION_CODE:
            query = (
                db.query(ActivationCode)
                .filter(
                    ActivationCode.sku_type == item.sku_type,
                    ActivationCode.status == CodeStatus.UNUSED,
                    ActivationCode.bound_account_email.is_(None),
                    ActivationCode.order_item_id.is_(None),
                )
                .order_by(ActivationCode.id.asc())
                .with_for_update(skip_locked=True)
            )
            codes = query.limit(missing).all()
            for code in codes:
                code.order_item_id = item.id
                db.add(code)
                delivery = OrderDelivery(
                    order_item_id=item.id,
                    kind="ACTIVATION_CODE",
                    code_id=code.id,
                    payload_enc=encrypt_text(code.code),
                )
                db.add(delivery)
                item.deliveries.append(delivery)
                missing -= 1
            if missing > 0:
                pending = True
        elif _is_cce_item(db, item):
            from app.services.fulfillment import cce_delivery

            variant = db.get(ProductVariant, item.variant_id) if item.variant_id else None
            valid_days = max(1, int((variant.months if variant else 1) * 30))
            for _ in range(missing):
                minted = cce_delivery.claim_and_mint_key(plan_tier="pro", valid_days=valid_days)
                if minted is None:
                    break
                delivery = OrderDelivery(
                    order_item_id=item.id,
                    kind="CCE_DELIVERY_KEY",
                    payload_enc=encrypt_text(minted["payload"]),
                )
                db.add(delivery)
                item.deliveries.append(delivery)
                missing -= 1
            if missing > 0:
                fallback_accounts = (
                    db.query(AccountInventory)
                    .filter(
                        AccountInventory.product_id == item.product_id,
                        AccountInventory.status == InventoryStatus.AVAILABLE,
                    )
                    .order_by(AccountInventory.id.asc())
                    .with_for_update(skip_locked=True)
                    .limit(missing)
                    .all()
                )
                for account in fallback_accounts:
                    account.status = InventoryStatus.SOLD
                    account.order_item_id = item.id
                    account.sold_at = utcnow()
                    db.add(account)
                    delivery = OrderDelivery(
                        order_item_id=item.id,
                        kind="ACCOUNT_CREDENTIALS",
                        account_id=account.id,
                        payload_enc=account.payload_enc,
                    )
                    db.add(delivery)
                    item.deliveries.append(delivery)
                    missing -= 1
            if missing > 0:
                pending = True
        elif _is_jit_item(db, item):
            # The account is upgraded after payment, so nothing is delivered at
            # this point; the job writes the credentials when the subscription is
            # confirmed. A missing pool is an operator problem, not a crash.
            pending = pending or True
            jit_items.append(item)
        else:
            accounts = (
                db.query(AccountInventory)
                .filter(
                    AccountInventory.product_id == item.product_id,
                    AccountInventory.status == InventoryStatus.AVAILABLE,
                )
                .order_by(AccountInventory.id.asc())
                .with_for_update(skip_locked=True)
                .limit(missing)
                .all()
            )
            for account in accounts:
                account.status = InventoryStatus.SOLD
                account.order_item_id = item.id
                account.sold_at = utcnow()
                db.add(account)
                delivery = OrderDelivery(
                    order_item_id=item.id,
                    kind="ACCOUNT_CREDENTIALS",
                    account_id=account.id,
                    payload_enc=account.payload_enc,
                )
                db.add(delivery)
                item.deliveries.append(delivery)
                missing -= 1
            if missing > 0:
                pending = True

    all_delivered = all(len(item.deliveries) >= item.quantity for item in order.items)
    if all_delivered:
        order.status = OrderStatus.DELIVERED
        order.delivered_at = order.delivered_at or utcnow()
        order.stock_pending = False
    else:
        order.status = OrderStatus.PAID
        order.stock_pending = True
    db.add(order)
    db.commit()
    db.refresh(order)

    if order.status == OrderStatus.DELIVERED and not was_delivered:
        delivered_count = sum(len(item.deliveries) for item in order.items)
        feishu.dispatch_event(
            "order.delivered",
            "MOLIPLY 自动交付完成",
            {
                "订单号": order.order_no,
                "商品": "、".join(item.product_name for item in order.items),
                "交付数量": delivered_count,
                "金额": f"¥{order.amount:.2f}",
            },
            severity="success",
            link_path=f"/admin/orders?q={order.order_no}",
        )
        threshold = runtime.get_int("feishu_low_stock_threshold", 5)
        if threshold > 0:
            checked: set[int] = set()
            for item in order.items:
                if item.product_id in checked:
                    continue
                checked.add(item.product_id)
                product = db.get(Product, item.product_id)
                if product is None:
                    continue
                remaining = available_stock(db, product)
                if remaining <= threshold:
                    feishu.dispatch_event(
                        "stock.low",
                        "MOLIPLY 库存不足预警",
                        {
                            "商品": product.name,
                            "剩余库存": remaining,
                            "预警阈值": threshold,
                        },
                        severity="warning",
                        link_path="/admin/products",
                    )
    elif order.stock_pending and not was_stock_pending:
        feishu.dispatch_event(
            "order.stock_pending",
            "MOLIPLY 订单已支付但缺货",
            {
                "订单号": order.order_no,
                "金额": f"¥{order.amount:.2f}",
                "商品": "、".join(item.product_name for item in order.items),
            },
            severity="warning",
            link_path=f"/admin/orders?q={order.order_no}",
        )
    for item in jit_items:
        try:
            jit_delivery.start_jit_delivery(db, item)
        except Exception:  # noqa: BLE001 - never fail the payment callback
            logger.exception("Could not queue JIT delivery for order item %s", item.id)
            feishu.dispatch_event(
                "recharge.manual_review",
                "MOLIPLY JIT 交付未能排队",
                {"订单号": order.order_no, "订单条目": item.id},
                severity="error",
                link_path=f"/admin/orders?q={order.order_no}",
            )
    return order


def _is_cce_item(db: Session, item: OrderItem) -> bool:
    product = db.get(Product, item.product_id)
    return bool(product and product.fulfillment_strategy == FulfillmentStrategy.CCE_ACCOUNT_KEY)


def _is_jit_item(db: Session, item: OrderItem) -> bool:
    """JIT only when the product asks for it *and* the engine is switched on.

    Turning the switch off degrades the product back to ordinary account stock,
    which is what an operator wants during a Playwright outage.
    """
    if not jit_delivery.jit_enabled():
        return False
    product = db.get(Product, item.product_id)
    return bool(product and product.fulfillment_strategy == FulfillmentStrategy.JIT_ACCOUNT_RECHARGE)


def finalize_pending_order_if_delivered(db: Session, order_item_id: int) -> None:
    """Close an order once its last pending line has been delivered.

    JIT deliveries arrive asynchronously long after the payment callback, so the
    order cannot be marked DELIVERED there. This re-checks the whole order and
    flips it only when every line is complete, then sends the delivery email.
    """
    order_id = (
        db.query(OrderItem.order_id)
        .filter(OrderItem.id == order_item_id)
        .scalar()
    )
    if order_id is None:
        return
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if order is None:
        return
    was_delivered = order.status == OrderStatus.DELIVERED
    # Count through fresh queries: the caller just committed a delivery in this
    # same session, so the relationship collections can still be stale.
    lines = db.query(OrderItem.id, OrderItem.quantity).filter(OrderItem.order_id == order.id).all()
    delivered = {
        item_id: count
        for item_id, count in (
            db.query(OrderDelivery.order_item_id, func.count(OrderDelivery.id))
            .filter(OrderDelivery.order_item_id.in_([line[0] for line in lines]))
            .group_by(OrderDelivery.order_item_id)
            .all()
        )
    }
    complete = all(delivered.get(item_id, 0) >= quantity for item_id, quantity in lines)
    if not complete:
        return
    order.status = OrderStatus.DELIVERED
    order.delivered_at = order.delivered_at or utcnow()
    order.stock_pending = False
    db.add(order)
    db.commit()
    db.refresh(order)
    if not was_delivered:
        _send_delivery_notification_async(db, order)


def cancel_order(db: Session, order: Order, *, actor: str = "admin", reason: str = "", ip: str = "") -> Order:
    order = db.query(Order).filter(Order.id == order.id).with_for_update().populate_existing().first()
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)
    if order.status != OrderStatus.PENDING:
        raise OrderError("ORDER_NOT_CANCELLABLE", "仅待支付订单可以取消", 409)
    order.status = OrderStatus.CANCELLED
    db.add(order)
    db.add(
        AuditLog(
            actor=actor,
            action="order.cancelled",
            entity_type="order",
            entity_id=order.order_no,
            detail=reason[:250] or "manual_cancel",
            ip=ip,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def refund_order(db: Session, order: Order, *, actor: str = "admin", reason: str = "", ip: str = "") -> dict:
    """Mark a paid/delivered order REFUNDED and disable any unused delivered activation codes."""
    order = (
        db.query(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.deliveries))
        .filter(Order.id == order.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)
    if order.status not in {OrderStatus.PAID, OrderStatus.DELIVERED}:
        raise OrderError("ORDER_NOT_REFUNDABLE", "仅已支付或已交付的订单可以退款", 409)

    revoked_codes = 0
    for item in order.items:
        for delivery in item.deliveries:
            if delivery.code_id:
                code = db.get(ActivationCode, delivery.code_id)
                if code is not None and code.status in {CodeStatus.UNUSED, CodeStatus.LOCKED}:
                    code.status = CodeStatus.DISABLED
                    code.note = (f"{code.note} | refunded" if code.note else "refunded")[:255]
                    db.add(code)
                    revoked_codes += 1

    order.status = OrderStatus.REFUNDED
    order.stock_pending = False
    db.add(order)
    db.add(
        AuditLog(
            actor=actor,
            action="order.refunded",
            entity_type="order",
            entity_id=order.order_no,
            detail=f"revoked_codes={revoked_codes} reason={reason[:200]}",
            ip=ip,
        )
    )
    db.commit()
    db.refresh(order)
    feishu.dispatch_event(
        "order.paid",
        "MOLIPLY 订单已退款",
        {
            "订单号": order.order_no,
            "金额": f"¥{order.amount:.2f}",
            "已作废未用卡密": revoked_codes,
            "原因": reason[:160] or "后台操作退款",
        },
        severity="warning",
        link_path=f"/admin/orders?q={order.order_no}",
    )
    return {"order": order, "revoked_codes": revoked_codes}


def fulfill_pending_backorders(db: Session, *, product_id: int | None = None, limit: int = 50) -> int:
    """FIFO auto-fulfillment for paid orders waiting on restocked inventory."""
    query = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PAID,
            Order.stock_pending.is_(True),
        )
        .order_by(Order.paid_at.asc(), Order.id.asc())
    )
    if product_id is not None:
        query = query.join(OrderItem, OrderItem.order_id == Order.id).filter(OrderItem.product_id == product_id)
    orders = query.distinct().limit(limit).all()
    fulfilled = 0
    for order in orders:
        try:
            updated = fulfill_order(db, order)
            if updated.status == OrderStatus.DELIVERED:
                fulfilled += 1
                _send_delivery_notification_async(db, updated)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-fulfillment failed for backorder %s", order.order_no)
            db.rollback()
    return fulfilled


def settle_mock_order(db: Session, order: Order, channel: str = "wechat") -> Order:
    if not mock_payments_allowed():
        raise OrderError("MOCK_PAYMENT_DISABLED", "模拟支付未启用", 403)
    mark_order_paid(db, order, f"mock_{channel}", f"MOCK-{secrets.token_hex(6)}")
    db.refresh(order)
    return order


def _payment_event_succeeded(event: PaymentEvent) -> bool:
    return bool((event.raw_json or {}).get("success"))


def _apply_success_event(
    db: Session,
    provider_key: str,
    parsed: Any,
    event_key: str,
    order: Order,
) -> None:
    """Record a successful notification, upgrading an earlier pending event.

    Gateways commonly send ``waiting``/``confirming`` before ``finished`` with
    the same transaction id. The earlier implementation stored every status as
    an immutable event, so the final success was swallowed as a duplicate.
    """
    for _ in range(3):
        existing = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.provider == provider_key, PaymentEvent.event_id == event_key)
            .first()
        )
        if existing and _payment_event_succeeded(existing):
            return
        try:
            if existing:
                existing.order_no = parsed.order_no
                existing.amount = parsed.amount
                existing.raw_json = {"success": True, "status": parsed.raw.get("status", "")}
                db.add(existing)
            else:
                db.add(
                    PaymentEvent(
                        provider=provider_key,
                        event_id=event_key,
                        order_no=parsed.order_no,
                        amount=parsed.amount,
                        verified=True,
                        raw_json={"success": True, "status": parsed.raw.get("status", "")},
                    )
                )
            db.flush()
        except IntegrityError:
            db.rollback()
            continue
        try:
            paid = mark_order_paid(db, order, parsed.provider, parsed.trade_no)
            if not paid:
                db.commit()
        except OrderError:
            db.rollback()
            raise
        return
    raise OrderError("PAYMENT_EVENT_RACE", "支付事件正在处理，请稍后重试", 409)


def process_payment_notify(
    db: Session,
    provider_key: str,
    payload: dict[str, Any],
    signature: str | None = None,
) -> str:
    """Verify, deduplicate and apply an asynchronous payment notification."""
    try:
        provider = get_callback_provider(provider_key)
        parsed = provider.parse_notify(payload, signature)
    except PaymentProviderError as exc:
        raise OrderError(exc.code, exc.message, 502) from exc
    if not parsed.verified:
        raise OrderError("PAYMENT_SIGNATURE_INVALID", "支付回调签名校验失败", 400)
    order = db.query(Order).filter(Order.order_no == parsed.order_no).first()
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "回调对应的订单不存在", 404)
    expected_amount = order.payment_amount
    expected_currency = (order.payment_currency or "").upper()
    if expected_amount is not None:
        if expected_currency and parsed.currency.upper() != expected_currency:
            raise OrderError("PAYMENT_CURRENCY_MISMATCH", "支付币种与订单不一致", 400)
        tolerance = Decimal("0.02") if expected_currency == "USD" else Decimal("0.01")
        if abs(parsed.amount - expected_amount) > tolerance:
            raise OrderError("PAYMENT_AMOUNT_MISMATCH", "支付金额与订单金额不一致", 400)
    elif not parsed.skip_amount_check and abs(parsed.amount - order.amount) > Decimal("0.01"):
        raise OrderError("PAYMENT_AMOUNT_MISMATCH", "支付金额与订单金额不一致", 400)

    event_key = parsed.trade_no or f"{parsed.order_no}:{parsed.amount:.2f}"
    existing = (
        db.query(PaymentEvent)
        .filter(PaymentEvent.provider == provider_key, PaymentEvent.event_id == event_key)
        .first()
    )
    if existing and _payment_event_succeeded(existing):
        return provider.success_body()

    if parsed.success:
        _apply_success_event(db, provider_key, parsed, event_key, order)
        return provider.success_body()

    # Pending/failed notifications are recorded for audit only; a later
    # success for the same event must still be allowed through.
    if existing is None:
        db.add(
            PaymentEvent(
                provider=provider_key,
                event_id=event_key,
                order_no=parsed.order_no,
                amount=parsed.amount,
                verified=True,
                raw_json={"success": False, "status": parsed.raw.get("status", "")},
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return provider.success_body()


def refund_and_revoke_order(
    db: Session,
    order_no: str,
    *,
    reason: str = "",
    actor: str = "admin",
    ip: str = "",
) -> dict[str, Any]:
    """Mark an order REFUNDED and immediately revoke delivered digital entitlements.

    - Disables any unused/locked ``ActivationCode`` bound to this order.
    - Remotely revokes any ``CCE_DELIVERY_KEY`` via CCE HTTP/SQLite so the buyer's
      Claude tunnel disconnects within 1 second.
    """
    from app.services.fulfillment import cce_delivery

    order = (
        db.query(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.deliveries))
        .filter(func.upper(Order.order_no) == order_no.strip().upper())
        .with_for_update()
        .first()
    )
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "订单不存在", 404)

    disabled_codes = 0
    revoked_cce_keys = 0
    for item in order.items:
        for delivery in item.deliveries:
            if delivery.kind == "ACTIVATION_CODE" and delivery.code_id:
                code = db.get(ActivationCode, delivery.code_id)
                if code and code.status != CodeStatus.DISABLED:
                    code.status = CodeStatus.DISABLED
                    db.add(code)
                    disabled_codes += 1
            elif delivery.kind == "CCE_DELIVERY_KEY":
                try:
                    raw_payload = decrypt_text(delivery.payload_enc)
                    if cce_delivery.revoke_delivery_payload(raw_payload):
                        revoked_cce_keys += 1
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to revoke CCE key for order %s", order.order_no, exc_info=False)

    order.status = OrderStatus.REFUNDED
    order.stock_pending = False
    db.add(order)
    db.add(
        AuditLog(
            actor=actor,
            action="order.refund_and_revoke",
            entity_type="order",
            entity_id=order.order_no,
            detail=f"codes_disabled={disabled_codes} cce_revoked={revoked_cce_keys} reason={reason[:120]}",
            ip=ip,
        )
    )
    db.commit()
    feishu.dispatch_event(
        "order.refunded",
        "MOLIPLY 订单退款并吊销权益",
        {
            "订单号": order.order_no,
            "金额": f"¥{order.amount:.2f}",
            "禁用卡密": disabled_codes,
            "吊销 CCE 隧道": revoked_cce_keys,
            "原因": reason or "管理员退款吊销",
        },
        severity="warning",
        link_path=f"/admin/orders?q={order.order_no}",
    )
    return {
        "order_no": order.order_no,
        "status": order.status.value,
        "disabled_codes": disabled_codes,
        "revoked_cce_keys": revoked_cce_keys,
    }
