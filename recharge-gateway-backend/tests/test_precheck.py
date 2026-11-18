"""Deep session pre-check: format, liveness, entitlement, zero consumption."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.models import ActivationCode, ChannelMode, CodeStatus, DeliveryType, Product, ProductCategory, SkuType
from app.services import precheck
from app.services.code_generator import generate_code


def _session(plan="free", plan_expires=None, email="probe@example.com", flag=None) -> str:
    payload = {
        "user": {"id": "u-1", "email": email},
        "expires": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z",
        "accessToken": "token-abc",
        "account": {"planType": plan},
    }
    if plan_expires is not None:
        payload["account"]["plan_expires_at"] = plan_expires
    if flag is not None:
        payload["account"]["is_paid_subscription_active"] = flag
    return json.dumps(payload)


def _code(db, sku=SkuType.CHATGPT_PLUS_1M, bound=None) -> ActivationCode:
    code = ActivationCode(
        code=generate_code(),
        sku_type=sku,
        channel_mode=ChannelMode.UPSTREAM,
        status=CodeStatus.UNUSED,
        bound_account_email=bound,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def _future(days=10) -> str:
    return (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"


def _past(days=2) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"


def test_active_subscription_is_rejected_with_the_offer_message(db):
    code = _code(db)
    result = precheck.precheck_session(code, _session(plan="plus", plan_expires=_future()))
    assert result.ok is False
    assert result.verdict == "ALREADY_SUBSCRIBED"
    assert "激活码未被消耗" in result.message
    assert "不支持有效期内叠加充值" in result.message


def test_expired_subscription_stays_redeemable(db):
    code = _code(db)
    result = precheck.precheck_session(code, _session(plan="plus", plan_expires=_past()))
    assert result.ok is True
    assert result.account_email == "p***@example.com"


def test_active_flag_without_a_window_still_blocks(db):
    code = _code(db)
    result = precheck.precheck_session(code, _session(plan="plus", flag=True))
    assert result.verdict == "ALREADY_SUBSCRIBED"


def test_window_key_variants_are_read(db):
    code = _code(db)
    for key in ("subscription_expires_at", "current_period_end", "paid_until"):
        raw = json.loads(_session(plan="pro", plan_expires=_future()))
        raw["account"][key] = raw["account"].pop("plan_expires_at")
        assert precheck.precheck_session(code, json.dumps(raw)).verdict == "ALREADY_SUBSCRIBED"


def test_free_account_passes(db):
    code = _code(db)
    assert precheck.precheck_session(code, _session()).ok is True


def test_malformed_json_is_reported_without_a_verdict_crash(db):
    code = _code(db)
    result = precheck.precheck_session(code, "{not-json")
    assert result.verdict == "INVALID_SESSION_TOKEN"
    assert precheck.status_for(result.verdict) == 400


def test_expired_session_credential_is_rejected(db):
    code = _code(db)
    raw = json.loads(_session())
    raw["expires"] = (datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z"
    result = precheck.precheck_session(code, json.dumps(raw))
    assert result.ok is False
    assert "过期" in result.message


def test_type_mismatch_is_reported(db):
    code = _code(db, sku=SkuType.CLAUDE_PRO_1M)
    result = precheck.precheck_session(code, _session())
    assert result.verdict == "SESSION_TYPE_MISMATCH"
    assert precheck.status_for(result.verdict) == 409


def test_bound_to_another_account_is_reported(db):
    code = _code(db, bound="someone-else@example.com")
    result = precheck.precheck_session(code, _session(email="probe@example.com"))
    assert result.verdict == "CODE_BOUND_TO_OTHER_ACCOUNT"


def test_no_session_skus_skip_the_credential_step(db):
    code = _code(db, sku=SkuType.GENERIC_DIGITAL)
    result = precheck.precheck_session(code, "")
    assert result.ok is True
    assert result.requires_session is False


def test_precheck_never_writes_or_consumes_the_code(client, db):
    code = _code(db)
    response = client.post(
        "/api/v1/redeem/parse-session",
        json={"code": code.code, "session_raw": _session(plan="plus", plan_expires=_future())},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_SUBSCRIBED"

    db.expire_all()
    fresh = db.query(ActivationCode).filter(ActivationCode.id == code.id).one()
    assert fresh.status is CodeStatus.UNUSED
    assert fresh.bound_account_email is None


def test_rejected_submit_leaves_the_code_usable_for_another_account(client, db):
    code = _code(db)
    rejected = client.post(
        "/api/v1/redeem/submit",
        json={"code": code.code, "session_raw": _session(plan="pro", plan_expires=_future(20))},
    )
    assert rejected.status_code == 409

    db.expire_all()
    fresh = db.query(ActivationCode).filter(ActivationCode.id == code.id).one()
    assert fresh.status == CodeStatus.UNUSED

    accepted = client.post(
        "/api/v1/redeem/submit",
        json={"code": code.code, "session_raw": _session(email="other@example.com")},
    )
    assert accepted.status_code == 200, accepted.text


def test_trial_card_submits_without_a_session(client, db):
    code = _code(db, sku=SkuType.GENERIC_DIGITAL)
    response = client.post("/api/v1/redeem/submit", json={"code": code.code, "session_raw": ""})
    assert response.status_code == 200, response.text
    db.expire_all()
    fresh = db.query(ActivationCode).filter(ActivationCode.id == code.id).one()
    assert fresh.status is CodeStatus.LOCKED


def test_verify_code_reports_that_a_session_is_optional(client, db):
    code = _code(db, sku=SkuType.GENERIC_DIGITAL)
    response = client.post("/api/v1/redeem/verify-code", json={"code": code.code})
    assert response.status_code == 200
    assert response.json()["requires_session"] is False


@pytest.mark.parametrize("plan", ["team", "enterprise", "business"])
def test_team_tiers_are_treated_as_paid(db, plan):
    code = _code(db)
    assert precheck.precheck_session(code, _session(plan=plan)).verdict == "ALREADY_SUBSCRIBED"
