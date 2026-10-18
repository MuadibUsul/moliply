from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas import CodeRequest, SessionRequest
from app.security import client_ip, limiter
from app.services import redeem as redeem_service

router = APIRouter(prefix="/api/v1/redeem", tags=["redeem"])


@router.post("/verify-code")
@limiter.limit(settings.redeem_rate_limit)
def verify_code(payload: CodeRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    return redeem_service.verify_code(db, payload.code, client_ip(request))


@router.post("/parse-session")
@limiter.limit(settings.redeem_rate_limit)
def parse_session(payload: SessionRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    return redeem_service.parse_and_precheck(db, payload.code, payload.session_raw, client_ip(request))


@router.post("/submit")
@limiter.limit(settings.redeem_rate_limit)
def submit(payload: SessionRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    task = redeem_service.submit_recharge(db, payload.code, payload.session_raw, client_ip(request))
    return {"task_id": task.task_id, "state": task.state.value}


@router.get("/status/{task_id}")
def status(task_id: str, db: Session = Depends(get_db)) -> dict:
    return redeem_service.get_task_status(db, task_id)
