from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.models import ActivationCode, ChannelMode, CodeStatus, RechargeState, RechargeTask, SkuType
from app.services.code_generator import generate_code
from app.services.errors import RechargeError
from app.services.redeem import run_recharge_task


def _session(email="user@example.com", plan="free"):
    return json.dumps(
        {
            "user": {"id": "user-1", "name": "Demo", "email": email},
            "expires": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
            "accessToken": "test-access-token",
            "account": {"planType": plan},
        }
    )


def _make_code(db, sku=SkuType.CHATGPT_PLUS_1M, channel=ChannelMode.UPSTREAM, bound_email=None):
    code = ActivationCode(
        code=generate_code(),
        sku_type=sku,
        channel_mode=channel,
        status=CodeStatus.UNUSED,
        bound_account_email=bound_email,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def _submit(client, code, email="user@example.com"):
    response = client.post(
        "/api/v1/redeem/submit",
        json={"code": code.code, "session_raw": _session(email)},
    )
    return response


def test_verify_parse_submit_and_succeed(client, db):
    code = _make_code(db)
    verify = client.post("/api/v1/redeem/verify-code", json={"code": code.code.lower()})
    assert verify.status_code == 200
    assert verify.json()["valid"] is True
    assert verify.json()["status"] == "UNUSED"

    parsed = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": _session()},
    )
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["eligible"] is True
    assert parsed.json()["account_email"] == "u***@example.com"

    submitted = _submit(client, code)
    assert submitted.status_code == 200, submitted.text
    task_id = submitted.json()["task_id"]
    assert submitted.json()["state"] == "PENDING"

    task = db.get(RechargeTask, task_id)
    assert task.encrypted_access_token
    db.refresh(code)
    assert code.status == CodeStatus.LOCKED
    assert code.bound_account_email == "user@example.com"

    run_recharge_task(task_id)

    db.expire_all()
    task = db.get(RechargeTask, task_id)
    code = db.get(ActivationCode, code.id)
    assert task.state == RechargeState.SUCCESS
    assert code.status == CodeStatus.USED
    assert code.used_at is not None
    assert task.encrypted_access_token is None
    assert task.encrypted_session_raw is None

    status = client.get(f"/api/v1/redeem/status/{task_id}")
    assert status.status_code == 200
    assert status.json()["state"] == "SUCCESS"
    assert status.json()["progress_percent"] == 100


def test_double_submit_is_idempotent_while_pending(client, db):
    code = _make_code(db)
    first = _submit(client, code)
    assert first.status_code == 200
    second = _submit(client, code)
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]


def test_submit_after_success_rejected(client, db):
    code = _make_code(db)
    submitted = _submit(client, code)
    task_id = submitted.json()["task_id"]
    run_recharge_task(task_id)
    again = _submit(client, code)
    assert again.status_code == 409
    assert again.json()["code"] == "CODE_ALREADY_USED"


def test_already_subscribed_is_blocked_without_locking(client, db):
    code = _make_code(db)
    response = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": _session(plan="plus")},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_SUBSCRIBED"
    db.refresh(code)
    assert code.status == CodeStatus.UNUSED
    assert code.bound_account_email is None


def test_code_bound_to_another_account(client, db):
    code = _make_code(db, bound_email="owner@example.com")
    response = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": _session("intruder@example.com")},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CODE_BOUND_TO_OTHER_ACCOUNT"


def test_transient_failure_releases_code(client, db, monkeypatch):
    code = _make_code(db)

    def fail(self, *args, **kwargs):
        raise RechargeError("UPSTREAM_TIMEOUT", "上游超时，激活码已释放", release_code=True)

    monkeypatch.setattr("app.services.upstream_provider.UpstreamProvider.create_recharge", fail)
    submitted = _submit(client, code)
    task_id = submitted.json()["task_id"]
    run_recharge_task(task_id)

    db.expire_all()
    task = db.get(RechargeTask, task_id)
    code = db.get(ActivationCode, code.id)
    assert task.state == RechargeState.FAILED
    assert task.error_code == "UPSTREAM_TIMEOUT"
    assert code.status == CodeStatus.UNUSED
    assert code.bound_account_email == "user@example.com"
    status = client.get(f"/api/v1/redeem/status/{task_id}").json()
    assert status["can_retry"] is True


def test_failure_without_release_keeps_code_locked(client, db, monkeypatch):
    code = _make_code(db)

    def fail(self, *args, **kwargs):
        raise RechargeError("UPSTREAM_TIMEOUT", "需要人工确认", release_code=False)

    monkeypatch.setattr("app.services.upstream_provider.UpstreamProvider.create_recharge", fail)
    task_id = _submit(client, code).json()["task_id"]
    run_recharge_task(task_id)

    db.expire_all()
    code = db.get(ActivationCode, code.id)
    assert code.status == CodeStatus.LOCKED
    assert client.get(f"/api/v1/redeem/status/{task_id}").json()["can_retry"] is False


def test_invalid_code_bruteforce_blocked(client):
    for _ in range(5):
        response = client.post("/api/v1/redeem/verify-code", json={"code": "MOLI-AAAA-BBBB-CCCC"})
        assert response.status_code == 404
    blocked = client.post("/api/v1/redeem/verify-code", json={"code": "MOLI-AAAA-BBBB-CCCC"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "IP_BLOCKED"


def test_disabled_code_rejected(client, db):
    code = _make_code(db)
    code.status = CodeStatus.DISABLED
    db.add(code)
    db.commit()
    response = client.post("/api/v1/redeem/verify-code", json={"code": code.code})
    assert response.status_code == 200
    assert response.json()["redeemable"] is False
    submit = _submit(client, code)
    assert submit.status_code == 409
    assert submit.json()["code"] == "CODE_DISABLED"


def test_claude_sku_session_kind(client, db):
    code = _make_code(db, sku=SkuType.CLAUDE_PRO_1M)
    wrong = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": _session()},
    )
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "SESSION_TYPE_MISMATCH"

    right = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": "sk-ant-sid01-abcdefghijklmnop"},
    )
    assert right.status_code == 200, right.text
    assert right.json()["session_kind"] == "claude"
