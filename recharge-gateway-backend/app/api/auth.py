from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.api.deps import require_user
from app.models import User
from app.schemas import ChangePasswordRequest, UserLoginRequest
from app.security import create_user_token, limiter
from app.services import accounts

router = APIRouter(prefix="/api/v1/auth", tags=["customer-auth"])


@router.post("/login")
@limiter.limit(settings.login_rate_limit)
def login(payload: UserLoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        user = accounts.authenticate_user(db, str(payload.email), payload.password)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    token, ttl = create_user_token(user)
    return {
        "token": token,
        "expires_in": ttl,
        "email": user.email,
    }


@router.get("/me")
def me(user: User = Depends(require_user)) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "created_at": user.created_at.isoformat() + "Z",
        "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
    }


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        user = accounts.change_password(db, user, payload.old_password, payload.new_password)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    token, ttl = create_user_token(user)
    return {"ok": True, "token": token, "expires_in": ttl}
