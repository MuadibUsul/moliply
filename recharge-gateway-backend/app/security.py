"""Authentication, rate-limiting and brute-force protection."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Rate limiter (Redis when reachable, in-process memory otherwise)
# ---------------------------------------------------------------------------
def _make_limiter() -> Limiter:
    storage_uri = settings.redis_url
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.4, socket_timeout=0.4)
        client.ping()
        logger.info("Rate limiter using Redis storage")
    except Exception:
        storage_uri = "memory://"
        logger.warning("Redis unavailable; rate limiter falls back to in-process memory storage")
    return Limiter(key_func=get_remote_address, storage_uri=storage_uri)


limiter = _make_limiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Failed-code brute-force tracker: 5 misses -> 30 minute block
# ---------------------------------------------------------------------------
class FailureTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures[key]
        window = settings.failed_code_window_minutes * 60
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        return bucket

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            bucket = self._prune(key, now)
            if len(bucket) >= settings.failed_code_limit:
                if now - bucket[-1] < settings.failed_code_window_minutes * 60:
                    return True
                bucket.clear()
            return False

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            bucket = self._prune(key, now)
            bucket.append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


failure_tracker = FailureTracker()


# ---------------------------------------------------------------------------
# Admin authentication (JWT signed with ADMIN_JWT_SECRET)
# ---------------------------------------------------------------------------
def verify_admin_password(password: str) -> bool:
    return secrets.compare_digest(password or "", settings.admin_password)


def create_admin_token(username: str) -> tuple[str, int]:
    ttl_seconds = settings.admin_token_ttl_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": "admin",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.admin_jwt_secret, algorithm="HS256")
    return token, ttl_seconds


def decode_admin_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.admin_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证") from exc
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return payload


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录运营后台")
    return decode_admin_token(credentials.credentials)
