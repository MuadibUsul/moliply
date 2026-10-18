"""Demo catalogue, activation-code stock and account inventory.

The seed only runs when the products table is empty. All credentials and card
codes below are fictitious development fixtures, never real accounts.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AccountInventory,
    ActivationCode,
    ChannelMode,
    CodeStatus,
    Coupon,
    DeliveryType,
    InventoryStatus,
    Product,
    ProductCategory,
    ProductVariant,
    SkuType,
)
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text, mask_email

logger = logging.getLogger(__name__)

FEATURES = [
    {"title": "自动发货", "desc": "付款完成后系统自动交付，无需等待人工。", "icon": "bolt"},
    {"title": "核销进度", "desc": "任务状态实时更新，充能进度随时可查。", "icon": "pulse"},
    {"title": "卡密验证", "desc": "一次性 MOLI 激活码，绑定账号后不可转移。", "icon": "shield"},
    {"title": "售后服务", "desc": "交付异常可提交售后，专业客服协助处理。", "icon": "headset"},
]


def _product(
    *,
    slug: str,
    name: str,
    subtitle: str,
    category: ProductCategory,
    brand: str,
    logo_key: str,
    delivery_type: DeliveryType,
    price: str,
    original_price: str,
    sold_count: int,
    badge: str | None,
    tags: list[str],
    warranty: str,
    delivery_note: str,
    description: str,
    usage_steps: list[str],
    faq: list[dict],
    variants: list[dict],
    sort_order: int,
) -> Product:
    product = Product(
        slug=slug,
        name=name,
        subtitle=subtitle,
        category=category,
        brand=brand,
        logo_key=logo_key,
        delivery_type=delivery_type,
        price=Decimal(price),
        original_price=Decimal(original_price),
        sold_count=sold_count,
        badge=badge,
        tags=tags,
        warranty=warranty,
        delivery_note=delivery_note,
        description=description,
        usage_steps=usage_steps,
        features=FEATURES,
        faq=faq,
        active=True,
        sort_order=sort_order,
    )
    for index, variant in enumerate(variants):
        product.variants.append(
            ProductVariant(
                label=variant["label"],
                months=variant.get("months", 1),
                price=Decimal(variant["price"]),
                original_price=Decimal(variant.get("original_price", variant["price"])),
                sku_type=SkuType(variant["sku_type"]) if variant.get("sku_type") else None,
                channel_mode=ChannelMode(variant.get("channel_mode", "UPSTREAM")),
                sort_order=index,
            )
        )
    return product


def seed_demo_data(db: Session) -> None:
    if db.query(Product.id).first():
        return

    logger.info("Seeding MOLIPLY demo catalogue")
    chatgpt_recharge_faq = [
        {
            "q": "MOLI 激活码如何使用？",
            "a": "付款后在订单详情复制 MOLI 激活码，前往免密自助充能中心，按页面指引粘贴 ChatGPT Session JSON 即可。",
        },
        {
            "q": "充值期间会泄露我的密码吗？",
            "a": "无需提供账号密码。Session 仅用于完成本次订阅升级，任务结束后系统会在 5 分钟内清除登录凭证。",
        },
        {
            "q": "为什么提示账号已是会员？",
            "a": "系统预检发现账号已处于 Plus/Pro 状态时会拦截充值，激活码不会被消耗，可直接用于其他账号。",
        },
        {
            "q": "充值失败会扣卡密吗？",
            "a": "因上游网络波动等可恢复原因失败时，激活码会自动释放，可在充能中心重试。",
        },
    ]

    chatgpt_recharge = _product(
        slug="chatgpt-plus-1m-recharge",
        name="ChatGPT Plus",
        subtitle="1个月代充 · MOLI 激活码",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="ChatGPT",
        logo_key="gpt",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price="158.00",
        original_price="199.00",
        sold_count=2368,
        badge="热销",
        tags=["免密代充", "自动核销", "24小时自动发货"],
        warranty="充值到账保障 · 不包个人违规封号",
        delivery_note="付款后自动发放 MOLI 激活码与充能中心链接",
        description=(
            "通过 MOLIPLY 商城购买后，系统自动发放 MOLI 激活码。"
            "无需提供 ChatGPT 账号密码，只需在免密自助充能中心提交浏览器 Session，即可由充能引擎完成 Plus 订阅升级。\n\n"
            "充能任务全程状态可追踪，账号凭证加密保存并在任务结束后自动清除。"
        ),
        usage_steps=[
            "在本页选择服务规格并完成支付",
            "在订单交付页复制 MOLI 激活码",
            "打开免密自助充能中心，验证激活码",
            "登录 ChatGPT 后复制 Session JSON 并提交",
            "等待自动充能完成，刷新 ChatGPT 页面确认权益",
        ],
        faq=chatgpt_recharge_faq,
        variants=[
            {"label": "1个月", "months": 1, "price": "158.00", "original_price": "199.00", "sku_type": "CHATGPT_PLUS_1M"},
            {"label": "3个月", "months": 3, "price": "435.00", "original_price": "597.00", "sku_type": "CHATGPT_PLUS_3M"},
            {"label": "12个月", "months": 12, "price": "1588.00", "original_price": "2388.00", "sku_type": "CHATGPT_PLUS_12M"},
        ],
        sort_order=1,
    )
    claude_account = _product(
        slug="claude-pro-account",
        name="Claude Pro",
        subtitle="1个月独享成品号 · 满月质保",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="Claude",
        logo_key="claude",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="188.00",
        original_price="218.00",
        sold_count=1268,
        badge="推荐",
        tags=["独享账号", "满月质保", "自动发货"],
        warranty="30 天质保 · 掉会员按天折算赔付",
        delivery_note="付款后自动发送邮箱、密码与辅助邮箱",
        description=(
            "Claude Pro 美区 iOS 内购成品号，开通独立的 Pro 会员权益。"
            "账号与辅助邮箱一并交付，可自行修改密码，适合需要稳定使用 Claude 的重度用户。\n\n"
            "成品号均为独立生产，一卡一号，不含共享账号。"
        ),
        usage_steps=[
            "下单并完成支付",
            "在订单详情查看完整账密",
            "首次登录后修改密码并绑定辅助邮箱",
            "如遇登录异常，保留订单号联系售后",
        ],
        faq=[
            {"q": "成品号和代充有什么区别？", "a": "成品号是已开通会员的独立账号，直接交付账密；代充是在你自己的账号上升级会员。"},
            {"q": "可以修改密码吗？", "a": "可以，交付后会同时提供辅助邮箱，建议首次登录后立即修改密码。"},
            {"q": "质保如何计算？", "a": "自交付日起 30 天内如因账号本身掉会员，按剩余天数折算赔付或补发。"},
        ],
        variants=[{"label": "1个月", "months": 1, "price": "188.00", "original_price": "218.00"}],
        sort_order=2,
    )
    chatgpt_account = _product(
        slug="chatgpt-plus-account",
        name="ChatGPT Plus",
        subtitle="独享成品号 · 满月质保",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="ChatGPT",
        logo_key="gpt",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="188.00",
        original_price="218.00",
        sold_count=986,
        badge="新品",
        tags=["独享账号", "满月质保", "自动发货"],
        warranty="30 天质保 · 掉会员按天折算赔付",
        delivery_note="付款后自动发送邮箱、密码与辅助邮箱",
        description=(
            "已开通 ChatGPT Plus 的独立账号，交付完整邮箱与密码，无需自己注册或准备海外邮箱。"
            "适合不想处理充值流程、希望即买即用的用户。\n\n"
            "账号采用美国账单与独立卡源生产，交付后请勿频繁切换地区或共享登录。"
        ),
        usage_steps=[
            "下单并完成支付",
            "订单详情复制完整账密",
            "登录 chatgpt.com 并确认 Plus 标识",
            "建议修改密码并妥善保管辅助邮箱",
        ],
        faq=[
            {"q": "登录需要邮箱验证码吗？", "a": "首次登录通常可直接使用交付的密码，如触发验证请使用交付的辅助邮箱或联系售后。"},
            {"q": "可以换绑自己的邮箱吗？", "a": "成品号不支持换绑主邮箱，请保留交付邮箱信息。"},
            {"q": "掉会员怎么办？", "a": "30 天质保期内按剩余天数折算赔付，具体以售后工单为准。"},
        ],
        variants=[{"label": "1个月", "months": 1, "price": "188.00", "original_price": "218.00"}],
        sort_order=3,
    )
    claude_recharge = _product(
        slug="claude-pro-1m-recharge",
        name="Claude Pro",
        subtitle="1个月免密代充 · MOLI 激活码",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="Claude",
        logo_key="claude",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price="168.00",
        original_price="198.00",
        sold_count=864,
        badge="热门",
        tags=["免密代充", "自动核销", "24小时自动发货"],
        warranty="充值到账保障",
        delivery_note="付款后自动发放 MOLI 激活码",
        description=(
            "为你的 Claude 账号升级 Pro 会员，无需提供登录密码。"
            "购买后获得 MOLI 激活码，在免密自助充能中心提交 Claude Session Key 即可。\n\n"
            "充值过程由上游通道完成，订单状态与充能进度全程可查。"
        ),
        usage_steps=[
            "下单并完成支付",
            "在订单交付页复制 MOLI 激活码",
            "前往免密自助充能中心验证激活码",
            "按指引粘贴 Claude Session Key 并提交",
            "等待上游通道完成充值",
        ],
        faq=[
            {"q": "Claude Session Key 在哪里获取？", "a": "登录 claude.ai 后打开开发者工具，在 Application/Cookies 中复制 sessionKey 的值。"},
            {"q": "需要提供密码吗？", "a": "不需要。仅提交临时 Session Key，任务结束后系统会清除凭证。"},
            {"q": "充值失败怎么办？", "a": "可恢复失败会自动释放激活码，重试即可；如持续失败请联系在线客服。"},
        ],
        variants=[
            {"label": "1个月", "months": 1, "price": "168.00", "original_price": "198.00", "sku_type": "CLAUDE_PRO_1M"}
        ],
        sort_order=4,
    )
    warranty_recharge = _product(
        slug="chatgpt-plus-warranty",
        name="ChatGPT Plus",
        subtitle="30天无忧质保版 · 正规卡源",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="ChatGPT",
        logo_key="gpt",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price="182.00",
        original_price="218.00",
        sold_count=532,
        badge="质保",
        tags=["30天质保", "正规卡源", "免密代充"],
        warranty="30 天卡源质保 · 掉会员全额重充或按天退款",
        delivery_note="付款后自动发放 MOLI 激活码与质保说明",
        description=(
            "使用更稳定的正规卡源，并提供 30 天卡源质保。"
            "若 30 天内因卡源问题导致会员掉线，可申请全额重充或按剩余天数退款。\n\n"
            "适合追求长期稳定、不希望反复售后的用户。"
        ),
        usage_steps=[
            "选择 30 天无忧质保版并完成下单",
            "复制订单中的 MOLI 激活码",
            "在充能中心提交 ChatGPT Session",
            "充值成功后保留订单号以享受质保",
        ],
        faq=chatgpt_recharge_faq
        + [
            {"q": "质保版和普通版有什么区别？", "a": "质保版使用更稳定的卡源，并额外提供 30 天卡源掉线保障。"},
            {"q": "如何申请质保？", "a": "凭订单号联系在线客服，核实掉会员原因后按政策重充或退款。"},
        ],
        variants=[
            {"label": "1个月", "months": 1, "price": "182.00", "original_price": "218.00", "sku_type": "CHATGPT_PLUS_1M"}
        ],
        sort_order=5,
    )
    cursor = _product(
        slug="cursor-pro",
        name="Cursor Pro",
        subtitle="1个月软件订阅 · 独立账号",
        category=ProductCategory.SOFTWARE,
        brand="Cursor",
        logo_key="cursor",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="118.00",
        original_price="148.00",
        sold_count=432,
        badge=None,
        tags=["开发工具", "自动发货"],
        warranty="首登保障",
        delivery_note="付款后自动发送 Cursor 账号信息",
        description="Cursor Pro 独立账号，适合需要 AI 编程助手的开发者。付款后自动交付账号信息，支持修改密码。",
        usage_steps=["下单支付", "在订单详情复制账号信息", "登录 cursor.com 并确认 Pro 状态"],
        faq=[{"q": "支持哪些设备？", "a": "Cursor 支持 Windows、macOS 与 Linux 客户端，登录同一账号即可使用。"}],
        variants=[{"label": "1个月", "months": 1, "price": "118.00", "original_price": "148.00"}],
        sort_order=6,
    )
    github = _product(
        slug="github-copilot",
        name="GitHub Copilot",
        subtitle="开发者会员服务 · 1个月",
        category=ProductCategory.DEVELOPER,
        brand="GitHub",
        logo_key="github",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="89.00",
        original_price="119.00",
        sold_count=321,
        badge=None,
        tags=["开发工具", "自动发货"],
        warranty="首登保障",
        delivery_note="付款后自动发送 GitHub 账号信息",
        description="GitHub Copilot 开发者会员账号，支持主流 IDE 插件。自动交付，开箱即用。",
        usage_steps=["下单支付", "复制账号信息", "在 IDE 中登录 GitHub Copilot"],
        faq=[{"q": "支持哪些 IDE？", "a": "支持 VS Code、JetBrains 系列、Visual Studio 等主流开发工具。"}],
        variants=[{"label": "1个月", "months": 1, "price": "89.00", "original_price": "119.00"}],
        sort_order=7,
    )
    midjourney = _product(
        slug="midjourney-standard",
        name="Midjourney",
        subtitle="1个月会员服务 · 标准版",
        category=ProductCategory.DESIGN,
        brand="Midjourney",
        logo_key="midjourney",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="128.00",
        original_price="158.00",
        sold_count=256,
        badge=None,
        tags=["设计创作", "自动发货"],
        warranty="首登保障",
        delivery_note="付款后自动发送 Midjourney 账号信息",
        description="Midjourney 标准会员账号，适合设计师与内容创作者。独立账号，交付即用。",
        usage_steps=["下单支付", "复制账号信息", "登录 Discord 或 Midjourney 网页版开始创作"],
        faq=[{"q": "如何开始生成图片？", "a": "登录交付的账号后进入 Midjourney 官方机器人或网页版即可开始创作。"}],
        variants=[{"label": "1个月", "months": 1, "price": "128.00", "original_price": "158.00"}],
        sort_order=8,
    )
    notion = _product(
        slug="notion-plus",
        name="Notion Plus",
        subtitle="效率软件订阅 · 1个月",
        category=ProductCategory.SOFTWARE,
        brand="Notion",
        logo_key="notion",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price="68.00",
        original_price="98.00",
        sold_count=198,
        badge=None,
        tags=["效率工具", "自动发货"],
        warranty="首登保障",
        delivery_note="付款后自动发送 Notion 账号信息",
        description="Notion Plus 独立账号，解锁无限文件上传与团队协作能力。",
        usage_steps=["下单支付", "复制账号信息", "登录 Notion 并确认 Plus 权益"],
        faq=[{"q": "数据可以导出吗？", "a": "可以，Notion 支持将工作区内容导出为 Markdown 或 HTML。"}],
        variants=[{"label": "1个月", "months": 1, "price": "68.00", "original_price": "98.00"}],
        sort_order=9,
    )
    api_credit = _product(
        slug="api-credit-100",
        name="API 额度",
        subtitle="开发者 API 服务 · 100 额度",
        category=ProductCategory.API,
        brand="OpenAI API",
        logo_key="api",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price="99.00",
        original_price="129.00",
        sold_count=156,
        badge=None,
        tags=["开发者", "自动发货"],
        warranty="到账保障",
        delivery_note="付款后自动发放 API 额度兑换码",
        description="开发者 API 额度兑换码，适用于接入大模型 API 的开发与测试场景。",
        usage_steps=["下单支付", "复制兑换码", "前往对应平台兑换额度"],
        faq=[{"q": "额度有效期多久？", "a": "以兑换平台规则为准，建议兑换后尽快使用。"}],
        variants=[
            {"label": "100 额度", "months": 1, "price": "99.00", "original_price": "129.00", "sku_type": "API_CREDIT_100"}
        ],
        sort_order=10,
    )

    products = [
        chatgpt_recharge,
        claude_account,
        chatgpt_account,
        claude_recharge,
        warranty_recharge,
        cursor,
        github,
        midjourney,
        notion,
        api_credit,
    ]
    db.add_all(products)
    db.flush()

    # Activation-code stock. Quantities are intentionally generous so the
    # demo can run end-to-end without manual replenishment. Production
    # deployments should set SEED_DEMO_INVENTORY=false and import real stock.
    stock_count = 0
    account_count = 0
    if settings.seed_demo_inventory:
        stock_plan = {
            SkuType.CHATGPT_PLUS_1M: 120,
            SkuType.CHATGPT_PLUS_3M: 30,
            SkuType.CHATGPT_PLUS_12M: 12,
            SkuType.CLAUDE_PRO_1M: 60,
            SkuType.API_CREDIT_100: 30,
        }
        seen: set[str] = set()
        for sku, count in stock_plan.items():
            for _ in range(count):
                code = generate_code()
                while code in seen:
                    code = generate_code()
                seen.add(code)
                db.add(
                    ActivationCode(
                        code=code,
                        sku_type=sku,
                        channel_mode=ChannelMode.UPSTREAM,
                        status=CodeStatus.UNUSED,
                        note="演示库存",
                    )
                )
        stock_count = sum(stock_plan.values())

        account_products = {
            "claude-pro-account": "claude",
            "chatgpt-plus-account": "chatgpt",
            "cursor-pro": "cursor",
            "github-copilot": "github",
            "midjourney-standard": "midjourney",
            "notion-plus": "notion",
        }
        for slug, prefix in account_products.items():
            product = next(item for item in products if item.slug == slug)
            for index in range(1, 7):
                email = f"demo.{prefix}.{index:03d}@moliply.test"
                payload = (
                    f"{email}----DemoPass!2026----backup.{prefix}.{index:03d}@moliply.test----2026-11-18"
                )
                db.add(
                    AccountInventory(
                        product_id=product.id,
                        label=mask_email(email),
                        payload_enc=encrypt_text(payload),
                        status=InventoryStatus.AVAILABLE,
                    )
                )
        account_count = len(account_products) * 6

        db.add_all(
            [
                Coupon(code="MOLI10", amount=Decimal("10.00"), min_amount=Decimal("100.00"), active=True),
                Coupon(code="WELCOME5", amount=Decimal("5.00"), min_amount=Decimal("50.00"), active=True),
            ]
        )

    db.commit()
    logger.info(
        "Seeded %s products, %s activation codes, %s account credentials",
        len(products),
        stock_count,
        account_count,
    )
