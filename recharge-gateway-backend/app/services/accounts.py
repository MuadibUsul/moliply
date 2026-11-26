"""Customer accounts created at checkout, used for order self-service."""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, utcnow
from app.services import locks, mailer

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class AccountError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError("PASSWORD_TOO_SHORT", f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError("PASSWORD_TOO_LONG", f"密码不能超过 {MAX_PASSWORD_LENGTH} 位")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_raw, r_raw, p_raw, salt_raw, digest_raw = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_raw),
            n=n,
            r=r,
            p=p,
            dklen=SCRYPT_DKLEN,
        )
        return hmac.compare_digest(candidate, _unb64(digest_raw))
    except Exception:
        return False


def find_user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == normalize_email(email)).first()


def get_or_create_user(db: Session, email: str, password: str) -> tuple[User, bool]:
    """Authenticate an existing checkout account or create a new one.

    Returns (user, created). A wrong password for an existing email is
    rejected so an order can never be attached to somebody else's account.
    """
    normalized = normalize_email(email)
    user = find_user(db, normalized)
    now = utcnow()
    if user:
        if not verify_password(password, user.password_hash):
            raise AccountError(
                "EMAIL_PASSWORD_MISMATCH",
                "该邮箱已注册，请输入正确的订单密码；如忘记密码可在「我的订单」页自助找回或联系客服重置",
                401,
            )
        user.last_login_at = now
        db.add(user)
        db.commit()
        db.refresh(user)
        return user, False

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        token_version=0,
        last_login_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = find_user(db, email)
    if not user or not verify_password(password, user.password_hash):
        raise AccountError("INVALID_CREDENTIALS", "邮箱或密码不正确", 401)
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, old_password: str, new_password: str) -> User:
    if not verify_password(old_password, user.password_hash):
        raise AccountError("INVALID_OLD_PASSWORD", "原密码不正确", 401)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


RESET_TTL_SECONDS = 600
RESET_COOLDOWN_SECONDS = 45
RESET_MAX_ATTEMPTS = 5
_RESET_LOCK = threading.Lock()
_RESET_STORE: OrderedDict[str, dict[str, float | int | str]] = OrderedDict()


def _code_digest(email: str, code: str) -> str:
    key = (settings.user_jwt_secret or settings.secret_key).encode("utf-8")
    msg = f"{normalize_email(email)}:{code.strip()}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _load_reset_entry(normalized_email: str) -> dict[str, float | int | str] | None:
    now = time.time()
    rclient = locks.redis_client()
    if rclient is not None:
        try:
            raw = rclient.get(f"moliply:pwreset:{normalized_email}")
            if raw:
                data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
                if float(data.get("expires_at", 0)) > now:
                    return data
        except Exception:  # noqa: BLE001
            pass
    with _RESET_LOCK:
        entry = _RESET_STORE.get(normalized_email)
        if entry and float(entry.get("expires_at", 0)) > now:
            return dict(entry)
        _RESET_STORE.pop(normalized_email, None)
        return None


def _save_reset_entry(normalized_email: str, entry: dict[str, float | int | str]) -> None:
    ttl = max(1, int(float(entry["expires_at"]) - time.time()))
    rclient = locks.redis_client()
    if rclient is not None:
        try:
            rclient.set(f"moliply:pwreset:{normalized_email}", json.dumps(entry), ex=ttl)
        except Exception:  # noqa: BLE001
            pass
    with _RESET_LOCK:
        _RESET_STORE[normalized_email] = dict(entry)
        _RESET_STORE.move_to_end(normalized_email)
        while len(_RESET_STORE) > 5000:
            _RESET_STORE.popitem(last=False)


def _clear_reset_entry(normalized_email: str) -> None:
    rclient = locks.redis_client()
    if rclient is not None:
        try:
            rclient.delete(f"moliply:pwreset:{normalized_email}")
        except Exception:  # noqa: BLE001
            pass
    with _RESET_LOCK:
        _RESET_STORE.pop(normalized_email, None)


def request_password_reset(db: Session, email: str) -> None:
    """Send a 6-digit password reset code if the account exists and SMTP is configured."""
    if not mailer.smtp_configured():
        raise AccountError(
            "SMTP_NOT_CONFIGURED",
            "系统暂未配置发信邮箱，请联系在线客服核实订单号后为您重置密码",
            409,
        )
    normalized = normalize_email(email)
    existing = _load_reset_entry(normalized)
    now = time.time()
    if existing and now - float(existing.get("issued_at", 0)) < RESET_COOLDOWN_SECONDS:
        raise AccountError("RESET_RATE_LIMITED", "验证码发送过于频繁，请稍后再试", 429)

    user = find_user(db, normalized)
    if not user:
        # Record a dummy cooldown entry so timing/rate-limit cannot enumerate emails.
        _save_reset_entry(
            normalized,
            {
                "digest": "",
                "issued_at": now,
                "expires_at": now + RESET_TTL_SECONDS,
                "attempts": RESET_MAX_ATTEMPTS,
            },
        )
        return

    code = f"{secrets.randbelow(1_000_000):06d}"
    entry: dict[str, float | int | str] = {
        "digest": _code_digest(normalized, code),
        "issued_at": now,
        "expires_at": now + RESET_TTL_SECONDS,
        "attempts": 0,
    }
    _save_reset_entry(normalized, entry)
    if not mailer.send_password_reset_email(user.email, code, ttl_minutes=RESET_TTL_SECONDS // 60):
        _clear_reset_entry(normalized)
        raise AccountError("EMAIL_SEND_FAILED", "验证码邮件发送失败，请稍后重试或联系在线客服", 502)


def reset_password_with_code(db: Session, email: str, code: str, new_password: str) -> User:
    """Verify the 6-digit email code and set a new password, revoking old sessions."""
    normalized = normalize_email(email)
    entry = _load_reset_entry(normalized)
    user = find_user(db, normalized)
    if not entry or not user or not entry.get("digest"):
        raise AccountError("INVALID_RESET_CODE", "验证码无效或已过期，请重新获取", 400)

    attempts = int(entry.get("attempts", 0))
    if attempts >= RESET_MAX_ATTEMPTS:
        _clear_reset_entry(normalized)
        raise AccountError("RESET_CODE_LOCKED", "验证码错误次数过多已失效，请重新获取", 400)

    expected = str(entry["digest"])
    candidate = _code_digest(normalized, code)
    if not hmac.compare_digest(expected, candidate):
        entry["attempts"] = attempts + 1
        if int(entry["attempts"]) >= RESET_MAX_ATTEMPTS:
            _clear_reset_entry(normalized)
        else:
            _save_reset_entry(normalized, entry)
        raise AccountError("INVALID_RESET_CODE", "验证码不正确，请核对邮件中的 6 位数字", 400)

    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 0) + 1
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    _clear_reset_entry(normalized)
    return user
