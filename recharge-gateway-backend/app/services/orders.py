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
    InventoryStatus,
    Order,
    OrderDelivery,
    OrderItem,
    OrderStatus,
    PaymentEvent,
    Product,
    utcnow,
)
from app.services import feishu
from app.services import runtime_settings as runtime
from app.services.catalog import available_sku_stock, available_stock, money
from app.services.crypto import decrypt_text, encrypt_text, mask_email
from app.services.mailer import send_delivery_email
from app.services.payments import get_callback_provider, get_provider, masked_order_title
from app.services.payments.base import PaymentProviderError
from app.services.payments.guards import mock_payments_allowed

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
    "ltzf_wechat": "微信支付",
    "ltzf_alipay": "支付宝",
    "xunhupay": "虎皮椒",
    "crypto": "加密货币",
    "nowpayments": "加密货币",
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


def validate_coupon(db: Session, code: str | None, amount: Decimal) -> tuple[Coupon | None, Decimal]:
    if not code:
        return None, Decimal("0")
    coupon = db.query(Coupon).filter(func.upper(Coupon.code) == code.strip().upper()).first()
    if not coupon or not coupon.active:
        raise OrderError("COUPON_INVALID", "优惠码无效")
    if coupon.expires_at and coupon.expires_at < utcnow():
        raise OrderError("COUPON_EXPIRED", "优惠码已过期")
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
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
    order = Order(
        order_no=generate_order_no(db),
        user_id=user_id,
        email=str(email).strip().lower(),
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
                else available_stock(db, product)
            )
            if stock < requested.quantity:
                raise OrderError("OUT_OF_STOCK", f"{product.name} 暂时缺货，请选择其他商品或稍后再试", 409)

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

    coupon, discount = validate_coupon(db, coupon_code, subtotal)
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


def create_payment(db: Session, order: Order, provider_name: str, client_ip: str):
    if order.status == OrderStatus.DELIVERED:
        raise OrderError("ORDER_ALREADY_PAID", "订单已支付并交付")
    if order.status != OrderStatus.PENDING:
        raise OrderError("ORDER_NOT_PENDING", "当前订单状态不可支付")
    if order.expires_at and order.expires_at < utcnow():
        order.status = OrderStatus.CANCELLED
        db.add(order)
        db.commit()
        raise OrderError("ORDER_EXPIRED", "订单已超时，请重新下单")
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
    if intent.provider == "crypto" and raw_amount is not None:
        order.payment_amount = Decimal(str(raw_amount))
        order.payment_currency = str(intent.raw.get("price_currency") or "USD").upper()
    elif intent.provider != "crypto":
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
    if order.status != OrderStatus.PENDING:
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
            action="order.paid",
            entity_type="order",
            entity_id=order.order_no,
            detail=f"provider={provider}",
        )
    )
    db.commit()
    db.refresh(order)
    fulfill_order(db, order)
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
    _send_delivery_notification_async(db, order)
    return True


def _send_delivery_notification_async(db: Session, order: Order) -> None:
    try:
        payload = serialize_order(db, order, reveal=True, reveal_email=True)
    except Exception:
        return
    if not smtp_configured_safe():
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
    return order


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
