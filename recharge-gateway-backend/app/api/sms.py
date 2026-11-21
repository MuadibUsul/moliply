"""Public SMS receipt endpoints.

All of them are keyed by the task id handed back by ``/lease``; the activation
code is only read once, at lease time. Rate limits match the redeem wizard so a
customer cannot hammer the supplier through us.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ActivationCode
from app.schemas import SmsChangeRequest, SmsLeaseRequest
from app.security import client_ip, limiter
from app.services import redeem as redeem_service
from app.services import sms as sms_service

router = APIRouter(prefix="/api/v1/sms", tags=["sms"])


@router.get("/options")
def options() -> dict:
    return sms_service.options()


@router.post("/lease")
@limiter.limit(settings.redeem_rate_limit)
def lease(payload: SmsLeaseRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    # Reuse the code lookup so invalid-format throttling and IP bans behave the
    # same as everywhere else in the redemption flow.
    normalized, code = redeem_service._load_code(db, payload.code, client_ip(request))
    task = sms_service.lease(db, code, payload.service, payload.country, client_ip(request))
    return {"task": sms_service.serialize(task, db.get(ActivationCode, task.code_id)), "code": normalized}


@router.get("/tasks/{task_id}")
@limiter.limit(settings.lookup_rate_limit)
def status(task_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    task = sms_service.poll(db, task_id)
    return {"task": sms_service.serialize(task, db.get(ActivationCode, task.code_id))}


@router.post("/tasks/{task_id}/change")
@limiter.limit(settings.redeem_rate_limit)
def change(task_id: str, payload: SmsChangeRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    task = sms_service.change_number(db, task_id, client_ip(request))
    return {"task": sms_service.serialize(task, db.get(ActivationCode, task.code_id))}
