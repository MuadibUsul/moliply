import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.config import settings
from app.database import get_db
from app.models import (
    CATEGORY_LABELS,
    DELIVERY_LABELS,
    Order,
    OrderStatus,
    Product,
    ProductCategory,
    User,
)
from app.schemas import CouponValidateRequest, OrderCreateRequest, PaymentCreateRequest
from app.security import client_ip, create_user_token, limiter
from app.services import accounts
from app.services import orders as order_service
from app.services import crypto_watcher
from app.services.catalog import list_categories, money, serialize_product
from app.services.payments import masked_order_title

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/store", tags=["store"])

SORT_OPTIONS = {
    "popular": (Product.sold_count.desc(), Product.sort_order.asc()),
    "price-asc": (Product.price.asc(), Product.id.asc()),
    "price-desc": (Product.price.desc(), Product.id.asc()),
    "newest": (Product.created_at.desc(), Product.id.desc()),
    "default": (Product.sort_order.asc(), Product.id.asc()),
}


@router.get("/categories")
def categories(db: Session = Depends(get_db)) -> dict:
    return {"items": list_categories(db)}


@router.get("/products")
def products(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    delivery: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=80),
    sort: str = Query(default="default"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=48),
) -> dict:
    query = db.query(Product).filter(Product.active.is_(True))
    if category and category.upper() != "ALL":
        try:
            query = query.filter(Product.category == ProductCategory(category.upper()))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知的商品分类")
    if delivery:
        query = query.filter(Product.delivery_type == delivery.upper())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(or_(Product.name.ilike(needle), Product.brand.ilike(needle), Product.subtitle.ilike(needle)))
    total = query.count()
    order_by = SORT_OPTIONS.get(sort, SORT_OPTIONS["default"])
    rows = query.order_by(*order_by).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [serialize_product(db, product) for product in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": list_categories(db),
        "delivery_options": [{"key": key, "label": label} for key, label in DELIVERY_LABELS.items()],
    }


@router.get("/products/{slug}")
def product_detail(slug: str, db: Session = Depends(get_db)) -> dict:
    query = db.query(Product).filter(Product.active.is_(True))
    if slug.isdigit():
        product = query.filter(Product.id == int(slug)).first()
    else:
        product = query.filter(Product.slug == slug).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return {"product": serialize_product(db, product)}


@router.post("/coupons/validate")
@limiter.limit(settings.lookup_rate_limit)
def coupon_validate(payload: CouponValidateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        coupon, discount = order_service.validate_coupon(db, payload.code, Decimal(str(payload.amount)))
    except order_service.OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {
        "valid": True,
        "code": coupon.code if coupon else "",
        "discount": money(discount),
        "message": f"优惠码已生效，立减 ¥{money(discount):.2f}",
    }


@router.post("/orders", status_code=201)
@limiter.limit("20/minute")
def create_order(payload: OrderCreateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user, account_created = accounts.get_or_create_user(db, str(payload.email), payload.password)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    order = order_service.create_order(
        db,
        email=user.email,
        user_id=user.id,
        items=payload.items,
        coupon_code=payload.coupon_code,
        client_ip=client_ip(request),
    )
    user_token, _ = create_user_token(user)
    return {
        "order": order_service.serialize_order(db, order, reveal=False, reveal_email=True),
        "payment_method": payload.payment_method,
        "user_token": user_token,
        "user_email": user.email,
        "account_created": account_created,
    }


@router.get("/my/orders")
def my_orders(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    orders = order_service.find_user_orders(db, user.id)
    return {
        "email": user.email,
        "items": [order_service.serialize_order(db, order, reveal=True) for order in orders],
    }


def _get_order(db: Session, order_no: str, user: User) -> Order:
    order = db.query(Order).filter(func.upper(Order.order_no) == order_no.strip().upper()).first()
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


@router.get("/orders/{order_no}")
def order_detail(
    order_no: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    order = _get_order(db, order_no, user)
    return {"order": order_service.serialize_order(db, order, reveal=True)}


@router.get("/orders/{order_no}/status")
def order_status(
    order_no: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    order = _get_order(db, order_no, user)
    payload = order_service.serialize_order(db, order, reveal=True)
    return {
        "order_no": order.order_no,
        "status": order.status.value,
        "status_label": payload["status_label"],
        "paid": order.status in {OrderStatus.PAID, OrderStatus.DELIVERED},
        "delivered": order.status == OrderStatus.DELIVERED,
        "stock_pending": order.stock_pending,
        "order": payload,
    }


@router.post("/orders/{order_no}/pay")
@limiter.limit("20/minute")
def pay(
    order_no: str,
    payload: PaymentCreateRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    order = _get_order(db, order_no, user)
    if order.status == OrderStatus.PENDING and order.expires_at:
        from app.models import utcnow

        if order.expires_at < utcnow():
            raise HTTPException(status_code=409, detail="订单已超时，请重新下单")
    return order_service.create_payment(db, order, payload.provider, client_ip(request), payload.coin)


@router.post("/orders/{order_no}/crypto-check")
@limiter.limit("6/minute")
def crypto_check(
    order_no: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    order = _get_order(db, order_no, user)
    if order.payment_provider not in {"usdt_trc20", "usdc_erc20", "eth", "btc"}:
        raise HTTPException(status_code=409, detail="该订单不是 USDT-TRC20 直收订单")
    crypto_watcher.scan_pending_orders(db)
    db.expire_all()
    order = _get_order(db, order_no, user)
    return {
        "matched": order.status in {OrderStatus.PAID, OrderStatus.DELIVERED},
        "order": order_service.serialize_order(db, order, reveal=True),
    }


@router.post("/orders/{order_no}/mock-settle")
@limiter.limit("30/minute")
def mock_settle(
    order_no: str,
    request: Request,
    channel: str = Query(default="wechat"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    order = _get_order(db, order_no, user)
    order_service.settle_mock_order(db, order, channel=channel)
    order = _get_order(db, order_no, user)
    return {"order": order_service.serialize_order(db, order, reveal=True)}


@router.get("/payment-title-preview")
def payment_title_preview(order_no: str = Query(default="MOL202610180001")) -> dict:
    """Transparency/debug endpoint: the exact descriptor sent to the gateway."""
    return {"title": masked_order_title(order_no), "categories": CATEGORY_LABELS}
