"""Authentication, rate-limiting and brute-force protection."""

from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter

from app.config import settings
from app.services import locks

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)
user_bearer_scheme = HTTPBearer(auto_error=False)

_MAX_MEMORY_BUCKETS = 10_000


def _parse_trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in settings.trusted_proxy_list:
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


def _is_trusted_proxy(ip_str: str) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _parse_trusted_networks())


def client_ip(request: Request) -> str:
    """Resolve real client IP without trusting spoofed X-Forwarded-For headers.

    Only honors ``X-Forwarded-For`` when the immediate peer is inside
    ``TRUSTED_PROXIES``, selecting the rightmost untrusted hop so an attacker
    cannot prepend arbitrary spoofed IPs.
    """
    peer = (request.client.host if request.client else "") or ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and _is_trusted_proxy(peer):
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        for hop in reversed(hops):
            if not _is_trusted_proxy(hop):
                return hop
        if hops:
            return hops[0]
    return peer or "unknown"


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
    return Limiter(key_func=client_ip, storage_uri=storage_uri)


limiter = _make_limiter()


# ---------------------------------------------------------------------------
# Failed-code brute-force tracker: 5 misses -> 30 minute block
# ---------------------------------------------------------------------------
class FailureTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    def _window_seconds(self) -> int:
        return max(60, settings.failed_code_window_minutes * 60)

    def _evict_if_needed(self) -> None:
        while len(self._failures) > _MAX_MEMORY_BUCKETS:
            self._failures.popitem(last=False)

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.get(key)
        if bucket is None:
            bucket = deque()
            self._failures[key] = bucket
            self._evict_if_needed()
        else:
            self._failures.move_to_end(key)
        window = self._window_seconds()
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        return bucket

    def is_blocked(self, key: str) -> bool:
        client = locks.redis_client()
        if client is not None:
            try:
                count = int(client.get(f"moliply:failcode:{key}") or 0)
                return count >= settings.failed_code_limit
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            now = time.time()
            bucket = self._prune(key, now)
            if len(bucket) >= settings.failed_code_limit:
                if now - bucket[-1] < self._window_seconds():
                    return True
                bucket.clear()
            return False

    def register_failure(self, key: str) -> None:
        client = locks.redis_client()
        if client is not None:
            try:
                rkey = f"moliply:failcode:{key}"
                pipe = client.pipeline()
                pipe.incr(rkey)
                pipe.expire(rkey, self._window_seconds())
                pipe.execute()
                return
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            now = time.time()
            bucket = self._prune(key, now)
            bucket.append(now)

    def clear(self, key: str) -> None:
        client = locks.redis_client()
        if client is not None:
            try:
                client.delete(f"moliply:failcode:{key}")
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._failures.pop(key, None)


failure_tracker = FailureTracker()


class AdminLoginGuard:
    """Consecutive-failure lockout for the admin account (Redis + bounded memory)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: OrderedDict[str, dict[str, float]] = OrderedDict()

    def _window(self) -> float:
        return max(1, settings.admin_login_lockout_minutes) * 60

    def _entry(self, key: str) -> dict[str, float]:
        now = time.time()
        entry = self._state.get(key)
        if entry is None:
            entry = {"count": 0.0, "last": 0.0, "blocked_until": 0.0}
            self._state[key] = entry
            while len(self._state) > _MAX_MEMORY_BUCKETS:
                self._state.popitem(last=False)
        else:
            self._state.move_to_end(key)
        if entry["blocked_until"] and entry["blocked_until"] <= now:
            entry["count"] = 0.0
            entry["blocked_until"] = 0.0
        elif entry["last"] and now - entry["last"] > self._window():
            entry["count"] = 0.0
        return entry

    def is_blocked(self, key: str) -> bool:
        client = locks.redis_client()
        if client is not None:
            try:
                return bool(client.exists(f"moliply:adminlock:blocked:{key}"))
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            entry = self._entry(key)
            return entry["blocked_until"] > time.time()

    def register_failure(self, key: str) -> int:
        limit = max(1, settings.admin_failed_login_limit)
        window = int(self._window())
        client = locks.redis_client()
        if client is not None:
            try:
                ckey = f"moliply:adminlock:count:{key}"
                count = int(client.incr(ckey))
                client.expire(ckey, window)
                if count >= limit:
                    client.set(f"moliply:adminlock:blocked:{key}", "1", ex=window)
                return count
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            entry = self._entry(key)
            entry["count"] += 1
            entry["last"] = time.time()
            if entry["count"] >= limit:
                entry["blocked_until"] = time.time() + self._window()
            return int(entry["count"])

    def clear(self, key: str) -> None:
        client = locks.redis_client()
        if client is not None:
            try:
                client.delete(f"moliply:adminlock:count:{key}", f"moliply:adminlock:blocked:{key}")
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._state.pop(key, None)

    def reset(self) -> None:
        client = locks.redis_client()
        if client is not None:
            try:
                for pattern in ("moliply:adminlock:count:*", "moliply:adminlock:blocked:*"):
                    keys = list(client.scan_iter(match=pattern, count=100))
                    if keys:
                        client.delete(*keys)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._state.clear()


admin_login_guard = AdminLoginGuard()


# ---------------------------------------------------------------------------
# Admin JWT revocation store (Redis + bounded in-memory fallback)
# ---------------------------------------------------------------------------
class _AdminRevocationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revoked: OrderedDict[str, float] = OrderedDict()

    def revoke(self, jti: str, exp: int) -> None:
        if not jti:
            return
        ttl = max(60, int(exp - time.time()))
        client = locks.redis_client()
        if client is not None:
            try:
                client.set(f"moliply:revoked_admin:{jti}", "1", ex=ttl)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._revoked[jti] = float(exp)
            self._revoked.move_to_end(jti)
            now = time.time()
            while len(self._revoked) > _MAX_MEMORY_BUCKETS:
                self._revoked.popitem(last=False)
            stale = [k for k, v in list(self._revoked.items()) if v <= now]
            for k in stale:
                self._revoked.pop(k, None)

    def is_revoked(self, jti: str) -> bool:
        if not jti:
            return False
        client = locks.redis_client()
        if client is not None:
            try:
                if client.exists(f"moliply:revoked_admin:{jti}"):
                    return True
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            exp = self._revoked.get(jti)
            if exp is None:
                return False
            if exp <= time.time():
                self._revoked.pop(jti, None)
                return False
            return True


_revocation_store = _AdminRevocationStore()


def verify_admin_totp(code: str) -> bool:
    secret = (settings.admin_totp_secret or "").strip()
    if not secret:
        if settings.is_production:
            logger.error("ADMIN_TOTP_SECRET is unset in production; failing closed")
            return False
        return True
    normalized = (code or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(normalized, valid_window=1)
    except Exception:
        logger.error("ADMIN_TOTP_SECRET is invalid; admin login is failing closed")
        return False


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
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.admin_jwt_secret, algorithm="HS256")
    return token, ttl_seconds


def revoke_admin_token(token: str) -> None:
    if not token:
        return
    try:
        payload = jwt.decode(
            token,
            settings.admin_jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return
    jti = str(payload.get("jti") or "")
    exp = int(payload.get("exp") or (time.time() + settings.admin_token_ttl_minutes * 60))
    if jti:
        _revocation_store.revoke(jti, exp)


def decode_admin_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.admin_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证") from exc
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    jti = str(payload.get("jti") or "")
    if jti and _revocation_store.is_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已注销，请重新登录")
    return payload


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录运营后台")
    return decode_admin_token(credentials.credentials)


# ---------------------------------------------------------------------------
# Customer order-account tokens
# ---------------------------------------------------------------------------
def _user_jwt_key() -> str:
    return settings.user_jwt_secret or settings.secret_key


def create_user_token(user: Any) -> tuple[str, int]:
    ttl_seconds = settings.user_token_ttl_days * 86400
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": "user",
        "ver": user.token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, _user_jwt_key(), algorithm="HS256")
    return token, ttl_seconds


def decode_user_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _user_jwt_key(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证") from exc
    if payload.get("role") != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return payload
