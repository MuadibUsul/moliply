from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import feishu
from app.services import orders as order_service
from app.services.payments.base import PaymentProviderError
from app.services.payments.wechat_native import HEADERS_KEY, RAW_BODY_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


async def _read_payload(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = dict(request.query_params)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload.update({key: value for key, value in body.items() if not isinstance(value, (dict, list))})
        except json.JSONDecodeError:
            pass
    elif "form" in content_type or "urlencoded" in content_type:
        form = await request.form()
        payload.update({key: str(value) for key, value in form.items()})
    return payload


def _report_callback_failure(channel: str, order_no: str, code: str, message: str) -> None:
    logger.warning("%s callback rejected: %s", channel, code)
    feishu.dispatch_event(
        "payment.callback_failed",
        f"{channel} 支付回调异常",
        {"订单号": order_no, "错误码": code, "说明": message},
        severity="error",
        link_path="/admin/orders",
    )


@router.post("/alipay/notify", response_class=PlainTextResponse)
async def alipay_notify(request: Request):
    """Alipay F2F asynchronous notification; Alipay expects the literal `success`."""
    payload = await _read_payload(request)
    db: Session = SessionLocal()
    try:
        body = order_service.process_payment_notify(db, "alipay", payload)
        logger.info("Alipay callback processed for order %s", payload.get("out_trade_no", ""))
        return PlainTextResponse(body)
    except order_service.OrderError as exc:
        _report_callback_failure("支付宝", str(payload.get("out_trade_no", "")), exc.code, exc.message)
        return PlainTextResponse("failure", status_code=400)
    except PaymentProviderError as exc:
        _report_callback_failure("支付宝", str(payload.get("out_trade_no", "")), exc.code, exc.message)
        return PlainTextResponse("failure", status_code=400)
    finally:
        db.close()


@router.post("/wechat/notify", response_class=PlainTextResponse)
async def wechat_notify(request: Request):
    """WeChat Pay V3 notification: signature headers + AEAD-encrypted resource."""
    raw = await request.body()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return PlainTextResponse('{"code":"FAIL","message":"invalid json"}', status_code=400)
    if not isinstance(envelope, dict):
        return PlainTextResponse('{"code":"FAIL","message":"invalid payload"}', status_code=400)
    payload: dict[str, Any] = dict(envelope)
    payload[RAW_BODY_KEY] = raw.decode("utf-8")
    payload[HEADERS_KEY] = {
        key: value for key, value in request.headers.items() if key.lower().startswith("wechatpay-")
    }
    db: Session = SessionLocal()
    try:
        body = order_service.process_payment_notify(db, "wechat", payload)
        logger.info("WeChat callback processed for order %s", envelope.get("resource", {}).get("original_type", ""))
        return PlainTextResponse(body)
    except order_service.OrderError as exc:
        _report_callback_failure("微信支付", "", exc.code, exc.message)
        return PlainTextResponse(f'{{"code":"FAIL","message":"{exc.message}"}}', status_code=400)
    except PaymentProviderError as exc:
        _report_callback_failure("微信支付", "", exc.code, exc.message)
        return PlainTextResponse(f'{{"code":"FAIL","message":"{exc.message}"}}', status_code=400)
    finally:
        db.close()


@router.post("/nowpayments/ipn", response_class=PlainTextResponse)
async def nowpayments_ipn(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return PlainTextResponse("FAIL", status_code=400)
    if not isinstance(payload, dict):
        return PlainTextResponse("FAIL", status_code=400)
    signature = request.headers.get("x-nowpayments-sig", "")
    db: Session = SessionLocal()
    try:
        body = order_service.process_payment_notify(db, "nowpayments", payload, signature)
        logger.info("NOWPayments callback processed for order %s", payload.get("order_id", ""))
        return PlainTextResponse(body)
    except order_service.OrderError as exc:
        _report_callback_failure("NOWPayments", str(payload.get("order_id", "")), exc.code, exc.message)
        return PlainTextResponse("FAIL", status_code=400)
    finally:
        db.close()
