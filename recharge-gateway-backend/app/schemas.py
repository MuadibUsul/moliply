from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CodeRequest(StrictModel):
    code: str = Field(min_length=4, max_length=40)


class SessionRequest(CodeRequest):
    session_raw: str = Field(min_length=8, max_length=262_144)


class OrderItemIn(StrictModel):
    product_id: int
    variant_id: int | None = None
    quantity: int = Field(default=1, ge=1, le=5)


class OrderCreateRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    items: list[OrderItemIn] = Field(min_length=1, max_length=10)
    coupon_code: str | None = Field(default=None, max_length=32)
    payment_method: Literal["wechat", "alipay", "crypto"] = "wechat"


class UserLoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(StrictModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AdminResetPasswordRequest(StrictModel):
    email: EmailStr
    new_password: str = Field(min_length=8, max_length=128)


class PaymentCreateRequest(StrictModel):
    provider: Literal["wechat", "alipay", "xunhupay", "crypto"] = "wechat"


class CouponValidateRequest(StrictModel):
    code: str = Field(min_length=1, max_length=32)
    amount: float = Field(ge=0)


class AdminLoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CodeGenerateRequest(StrictModel):
    count: int = Field(default=10, ge=1, le=1000)
    sku_type: Literal[
        "CHATGPT_PLUS_1M",
        "CHATGPT_PLUS_3M",
        "CHATGPT_PLUS_12M",
        "CLAUDE_PRO_1M",
        "CHATGPT_PRO_200",
        "API_CREDIT_100",
        "GENERIC_DIGITAL",
    ] = "CHATGPT_PLUS_1M"
    channel_mode: Literal["UPSTREAM", "SELF_VCC"] = "UPSTREAM"
    note: str = Field(default="", max_length=200)


class CodeImportRequest(StrictModel):
    text: str = Field(min_length=1, max_length=200_000)
    sku_type: Literal[
        "CHATGPT_PLUS_1M",
        "CHATGPT_PLUS_3M",
        "CHATGPT_PLUS_12M",
        "CLAUDE_PRO_1M",
        "CHATGPT_PRO_200",
        "API_CREDIT_100",
        "GENERIC_DIGITAL",
    ] = "CHATGPT_PLUS_1M"
    channel_mode: Literal["UPSTREAM", "SELF_VCC"] = "UPSTREAM"
    note: str = Field(default="", max_length=200)


class CodeStatusRequest(StrictModel):
    status: Literal["UNUSED", "DISABLED"]


class BillingAddress(StrictModel):
    line1: str = Field(min_length=3, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=2, max_length=40)
    postal_code: str = Field(min_length=3, max_length=16)
    country: Literal["US"] = "US"
    line2: str = Field(default="", max_length=120)


class VccCardCreateRequest(StrictModel):
    provider: str = Field(default="RDVCC", max_length=32)
    card_number: str = Field(min_length=12, max_length=25)
    cvv: str = Field(min_length=3, max_length=4)
    exp_month: str = Field(min_length=1, max_length=2)
    exp_year: str = Field(min_length=2, max_length=4)
    holder_name: str = Field(min_length=1, max_length=64)
    billing_address: BillingAddress
    available_balance: float = Field(ge=0, le=100_000)
    max_bind_count: int = Field(default=1, ge=1, le=2)

    @field_validator("exp_month", "exp_year")
    @classmethod
    def digits_only(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("有效期必须为数字")
        return value


class VccCardUpdateRequest(StrictModel):
    status: Literal["ACTIVE", "SUSPENDED", "EXHAUSTED"] | None = None
    available_balance: float | None = Field(default=None, ge=0, le=100_000)
    reset_failed_count: bool = False


class ProductUpdateRequest(StrictModel):
    active: bool | None = None
    price: float | None = Field(default=None, ge=0, le=100_000)
    badge: str | None = Field(default=None, max_length=32)


class AccountImportRequest(StrictModel):
    product_id: int
    text: str = Field(min_length=3, max_length=200_000)

class SettingsUpdateRequest(StrictModel):
    values: dict[str, str | int | float | bool] = Field(default_factory=dict)
    clear_secrets: list[str] = Field(default_factory=list, max_length=20)
