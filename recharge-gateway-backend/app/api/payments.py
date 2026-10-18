from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import orders as order_service
from app.services.payments.base import PaymentProviderError

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


@router.post("/ltzf/notify", response_class=PlainTextResponse)
async def ltzf_notify(request: Request):
    payload = await _read_payload(request)
    db: Session = SessionLocal()
    try:
        body = order_service.process_payment_notify(db, "ltzf", payload)
        logger.info("Lanttu callback processed for order %s", payload.get("out_trade_no", ""))
        return PlainTextResponse(body)
    except order_service.OrderError as exc:
        logger.warning("Lanttu callback rejected: %s", exc.code)
        return PlainTextResponse("FAIL", status_code=400)
    except PaymentProviderError as exc:
        logger.warning("Lanttu callback provider error: %s", exc.code)
        return PlainTextResponse("FAIL", status_code=400)
    finally:
        db.close()


@router.post("/xunhupay/notify", response_class=PlainTextResponse)
async def xunhupay_notify(request: Request):
    payload = await _read_payload(request)
    db: Session = SessionLocal()
    try:
        body = order_service.process_payment_notify(db, "xunhupay", payload)
        logger.info("Xunhupay callback processed for order %s", payload.get("trade_order_id", ""))
        return PlainTextResponse(body)
    except order_service.OrderError as exc:
        logger.warning("Xunhupay callback rejected: %s", exc.code)
        return PlainTextResponse("fail", status_code=400)
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
        logger.warning("NOWPayments callback rejected: %s", exc.code)
        return PlainTextResponse("FAIL", status_code=400)
    finally:
        db.close()
