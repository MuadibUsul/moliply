from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp, stored consistently across SQLite/PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enum_column(enum_cls: type[enum.Enum], length: int = 32) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


class DeliveryType(str, enum.Enum):
    ACTIVATION_CODE = "ACTIVATION_CODE"
    ACCOUNT_CREDENTIALS = "ACCOUNT_CREDENTIALS"


class ProductCategory(str, enum.Enum):
    AI_MEMBERSHIP = "AI_MEMBERSHIP"
    SOFTWARE = "SOFTWARE"
    DEVELOPER = "DEVELOPER"
    DESIGN = "DESIGN"
    DIGITAL = "DIGITAL"
    API = "API"


CATEGORY_LABELS: dict[str, str] = {
    "AI_MEMBERSHIP": "AI 会员",
    "SOFTWARE": "软件订阅",
    "DEVELOPER": "开发工具",
    "DESIGN": "设计创作",
    "DIGITAL": "数字商品",
    "API": "API 额度",
}

DELIVERY_LABELS: dict[str, str] = {
    "ACTIVATION_CODE": "MOLI 激活码",
    "ACCOUNT_CREDENTIALS": "账密直发",
}


class CodeStatus(str, enum.Enum):
    UNUSED = "UNUSED"
    LOCKED = "LOCKED"
    USED = "USED"
    DISABLED = "DISABLED"


class ChannelMode(str, enum.Enum):
    # Legacy V1 values are retained for existing codes and API compatibility.
    UPSTREAM = "UPSTREAM"
    SELF_VCC = "SELF_VCC"
    UPSTREAM_ONLY = "UPSTREAM_ONLY"
    SELF_VCC_ONLY = "SELF_VCC_ONLY"
    HYBRID_SELF_FIRST = "HYBRID_SELF_FIRST"


class RegionPreference(str, enum.Enum):
    PH_FIRST = "PH_FIRST"
    US_ONLY = "US_ONLY"


class SkuType(str, enum.Enum):
    CHATGPT_PLUS_1M = "CHATGPT_PLUS_1M"
    CHATGPT_PLUS_3M = "CHATGPT_PLUS_3M"
    CHATGPT_PLUS_12M = "CHATGPT_PLUS_12M"
    CLAUDE_PRO_1M = "CLAUDE_PRO_1M"
    CHATGPT_PRO_200 = "CHATGPT_PRO_200"
    CHATGPT_PRO_20X_1M = "CHATGPT_PRO_20X_1M"
    API_CREDIT_100 = "API_CREDIT_100"
    GENERIC_DIGITAL = "GENERIC_DIGITAL"


SKU_LABELS: dict[str, str] = {
    "CHATGPT_PLUS_1M": "ChatGPT Plus · 1 个月",
    "CHATGPT_PLUS_3M": "ChatGPT Plus · 3 个月",
    "CHATGPT_PLUS_12M": "ChatGPT Plus · 12 个月",
    "CLAUDE_PRO_1M": "Claude Pro · 1 个月",
    "CHATGPT_PRO_200": "ChatGPT Pro · 1 个月",
    "CHATGPT_PRO_20X_1M": "ChatGPT Pro 20x · 1 个月",
    "API_CREDIT_100": "API 额度 · 100",
    "GENERIC_DIGITAL": "通用数字权益",
}


class RechargeState(str, enum.Enum):
    PENDING = "PENDING"
    CHECKING_ACCOUNT = "CHECKING_ACCOUNT"
    CREATING_CHECKOUT = "CREATING_CHECKOUT"
    PAYING = "PAYING"
    AWAITING_3DS = "AWAITING_3DS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class InventoryStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    SOLD = "SOLD"
    DISABLED = "DISABLED"


class VccStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(96), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[ProductCategory] = mapped_column(enum_column(ProductCategory, 32), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    logo_key: Mapped[str] = mapped_column(String(32), nullable=False, default="gpt")
    delivery_type: Mapped[DeliveryType] = mapped_column(enum_column(DeliveryType), nullable=False)
    badge: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warranty: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    delivery_note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    features: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    faq: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.sort_order",
        lazy="selectin",
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    sku_type: Mapped[SkuType | None] = mapped_column(enum_column(SkuType, 32), nullable=True)
    channel_mode: Mapped[ChannelMode] = mapped_column(enum_column(ChannelMode), nullable=False, default=ChannelMode.UPSTREAM)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    product: Mapped[Product] = relationship(back_populates="variants")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    coupon_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus), nullable=False, default=OrderStatus.PENDING, index=True)
    payment_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_trade_no: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    payment_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    payment_address: Mapped[str | None] = mapped_column(String(96), nullable=True)
    payment_network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    client_ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    stock_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
        lazy="selectin",
    )
    user: Mapped["User"] = relationship(back_populates="orders")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    spec_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    logo_key: Mapped[str] = mapped_column(String(32), nullable=False, default="gpt")
    brand: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    delivery_type: Mapped[DeliveryType] = mapped_column(enum_column(DeliveryType), nullable=False)
    sku_type: Mapped[SkuType | None] = mapped_column(enum_column(SkuType, 32), nullable=True)
    channel_mode: Mapped[ChannelMode] = mapped_column(enum_column(ChannelMode), nullable=False, default=ChannelMode.UPSTREAM)

    order: Mapped[Order] = relationship(back_populates="items")
    deliveries: Mapped[list["OrderDelivery"]] = relationship(
        back_populates="order_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    sku_type: Mapped[SkuType] = mapped_column(enum_column(SkuType, 32), nullable=False, index=True)
    channel_mode: Mapped[ChannelMode] = mapped_column(enum_column(ChannelMode), nullable=False, default=ChannelMode.UPSTREAM)
    preferred_region: Mapped[RegionPreference] = mapped_column(
        enum_column(RegionPreference, 16), nullable=False, default=RegionPreference.PH_FIRST
    )
    status: Mapped[CodeStatus] = mapped_column(enum_column(CodeStatus), nullable=False, default=CodeStatus.UNUSED, index=True)
    bound_account_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id"), nullable=True, index=True)
    upstream_code_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AccountInventory(Base):
    __tablename__ = "account_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    payload_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[InventoryStatus] = mapped_column(enum_column(InventoryStatus), nullable=False, default=InventoryStatus.AVAILABLE, index=True)
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OrderDelivery(Base):
    __tablename__ = "order_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    code_id: Mapped[int | None] = mapped_column(ForeignKey("activation_codes.id"), nullable=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("account_inventory.id"), nullable=True)
    payload_enc: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    order_item: Mapped[OrderItem] = relationship(back_populates="deliveries")


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RechargeTask(Base):
    __tablename__ = "recharge_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_id: Mapped[int] = mapped_column(ForeignKey("activation_codes.id"), nullable=False, index=True)
    target_email: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    target_user_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_session_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    executed_region: Mapped[str | None] = mapped_column(String(4), nullable=True)
    billed_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    billed_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    session_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="chatgpt")
    state: Mapped[RechargeState] = mapped_column(enum_column(RechargeState), nullable=False, default=RechargeState.PENDING, index=True)
    upstream_order_no: Mapped[str | None] = mapped_column(String(96), nullable=True)
    vcc_card_id: Mapped[int | None] = mapped_column(ForeignKey("vcc_cards.id"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VccCard(Base):
    __tablename__ = "vcc_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="RDVCC")
    card_bin: Mapped[str] = mapped_column(String(8), nullable=False)
    card_number_enc: Mapped[str] = mapped_column(Text, nullable=False)
    cvv_enc: Mapped[str] = mapped_column(Text, nullable=False)
    exp_month: Mapped[str] = mapped_column(String(4), nullable=False)
    exp_year: Mapped[str] = mapped_column(String(4), nullable=False)
    holder_name: Mapped[str] = mapped_column(String(64), nullable=False)
    billing_address_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    ph_billing_address_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    three_ds_email_account: Mapped[str | None] = mapped_column(String(128), nullable=True)
    available_balance: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_bind_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    current_bind_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[VccStatus] = mapped_column(enum_column(VccStatus), nullable=False, default=VccStatus.ACTIVE, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_payment_provider_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="feishu")
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent", index=True)
    error: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)


__all__ = [
    "AccountInventory",
    "ActivationCode",
    "AuditLog",
    "CATEGORY_LABELS",
    "ChannelMode",
    "CodeStatus",
    "Coupon",
    "DELIVERY_LABELS",
    "DeliveryType",
    "InventoryStatus",
    "Order",
    "OrderDelivery",
    "OrderItem",
    "OrderStatus",
    "NotificationLog",
    "PaymentEvent",
    "PlatformSetting",
    "Product",
    "ProductCategory",
    "ProductVariant",
    "RechargeState",
    "RechargeTask",
    "RegionPreference",
    "SKU_LABELS",
    "SkuType",
    "User",
    "VccCard",
    "VccStatus",
    "utcnow",
]
