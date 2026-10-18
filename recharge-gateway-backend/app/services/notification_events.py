"""Catalogue of admin notification events exposed in the settings UI."""

from __future__ import annotations

FEISHU_EVENTS: list[dict[str, str]] = [
    {
        "key": "order.created",
        "label": "新订单",
        "description": "客户创建订单时通知（含商品、金额、邮箱掩码）",
        "severity": "info",
    },
    {
        "key": "order.paid",
        "label": "支付成功",
        "description": "支付回调或人工确认后通知",
        "severity": "success",
    },
    {
        "key": "order.delivered",
        "label": "自动交付完成",
        "description": "订单卡密/成品号自动发货完成",
        "severity": "success",
    },
    {
        "key": "order.stock_pending",
        "label": "已支付但缺货",
        "description": "支付成功但库存不足，需要补货",
        "severity": "warning",
    },
    {
        "key": "recharge.submitted",
        "label": "新充能任务",
        "description": "客户提交 MOLI 激活码与 Session",
        "severity": "info",
    },
    {
        "key": "recharge.success",
        "label": "充值成功",
        "description": "充值任务完成并核销",
        "severity": "success",
    },
    {
        "key": "recharge.failed",
        "label": "充值失败",
        "description": "可恢复失败或通道异常",
        "severity": "warning",
    },
    {
        "key": "recharge.manual_review",
        "label": "充值需人工复核",
        "description": "支付结果不确定、激活码保持锁定",
        "severity": "error",
    },
    {
        "key": "stock.low",
        "label": "库存不足预警",
        "description": "自动交付后库存低于阈值",
        "severity": "warning",
    },
    {
        "key": "payment.callback_failed",
        "label": "支付回调异常",
        "description": "签名、金额或订单校验失败",
        "severity": "error",
    },
    {
        "key": "system.error",
        "label": "系统任务异常",
        "description": "充值任务或后台任务出现未处理异常",
        "severity": "error",
    },
    {
        "key": "daily.summary",
        "label": "每日经营日报",
        "description": "每天 09:00 汇总订单、收入、交付与待处理事项",
        "severity": "info",
    },
]

FEISHU_EVENT_KEYS = {item["key"] for item in FEISHU_EVENTS}
DEFAULT_FEISHU_EVENTS = ",".join(item["key"] for item in FEISHU_EVENTS)
