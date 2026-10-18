"""Customer accounts created at checkout, used for order self-service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from sqlalchemy.orm import Session

from app.models import User, utcnow

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
                "该邮箱已注册，请输入正确的订单密码；如忘记密码请联系客服重置",
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
