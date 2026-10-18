from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "moliply_test_platform.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "false",
        "DATABASE_URL": f"sqlite:///{TEST_DB.as_posix()}",
        "REDIS_URL": "redis://127.0.0.1:6399/0",
        "TASK_MODE": "manual",
        "SEED_DEMO_DATA": "true",
        "PAYMENT_MODE": "mock",
        "MOCK_PAYMENT_ENABLED": "true",
        "UPSTREAM_MODE": "mock",
        "UPSTREAM_MOCK_DELAY_SECONDS": "0",
        "UPSTREAM_MOCK_FAIL_RATE": "0",
        "OPENAI_ACCOUNT_CHECK_ENABLED": "false",
        "SELF_VCC_ENABLED": "false",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "test-admin-pass",
        "ADMIN_JWT_SECRET": "test-admin-jwt-secret-0123456789abcdef",
        "USER_JWT_SECRET": "test-user-jwt-secret-0123456789abcdef",
        "LOGIN_RATE_LIMIT": "1000/minute",
        "MASTER_AES_KEY": base64.b64encode(b"m" * 32).decode("ascii"),
        "SECRET_KEY": "test-secret-0123456789abcdef",
        "REDEEM_RATE_LIMIT": "1000/minute",
        "ADMIN_LOGIN_RATE_LIMIT": "1000/minute",
        "LOOKUP_RATE_LIMIT": "1000/minute",
        "FAILED_CODE_LIMIT": "5",
        "FAILED_CODE_WINDOW_MINUTES": "30",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.security import failure_tracker  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_failures():
    with failure_tracker._lock:
        failure_tracker._failures.clear()
    yield


@pytest.fixture(scope="session")
def admin_headers(client):
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": "test-admin-pass"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def pytest_sessionfinish(session, exitstatus):
    try:
        TEST_DB.unlink()
    except OSError:
        pass

@pytest.fixture(autouse=True)
def _reset_runtime_settings():
    """Keep tests isolated from DB-backed admin settings and the 2s cache."""
    from app.database import SessionLocal
    from app.models import PlatformSetting
    from app.services import runtime_settings

    with SessionLocal() as db:
        db.query(PlatformSetting).delete()
        db.commit()
    runtime_settings.refresh()
    yield
    with SessionLocal() as db:
        db.query(PlatformSetting).delete()
        db.commit()
    runtime_settings.refresh()
