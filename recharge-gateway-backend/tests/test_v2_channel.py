"""V2 incremental self-owned channel: routing, region, fallback and admin API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    RechargeState,
    RechargeTask,
    RegionPreference,
    SkuType,
    VccCard,
    VccStatus,
)
from app.services import channel_dispatcher, region_router, runtime_settings, vcc_pool_scheduler
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.redeem import run_recharge_task
from app.services.self_vcc_worker import SelfVccFailure
from app.services.session_parser import SessionInfo
from app.services.upstream_provider import UpstreamProvider, UpstreamResult


@pytest.fixture(autouse=True)
def _clean_card_pool(db):
    db.query(VccCard).update({VccCard.status: VccStatus.SUSPENDED})
    db.commit()
    yield


def _session(email: str = "user@example.com", currency_hint: str | None = None) -> SessionInfo:
    raw = json.dumps(
        {
            "user": {"id": "user-1", "name": "Demo", "email": email},
            "expires": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
            "accessToken": "test-access-token",
            "account": {"planType": "free"},
        }
    )
    return SessionInfo(
        kind="chatgpt",
        email=email,
        user_id="user-1",
        access_token="test-access-token",
        raw=raw,
        expires_at=datetime.utcnow() + timedelta(hours=2),
        plan="free",
        currency_hint=currency_hint,
    )


def _code(
    db,
    channel: ChannelMode = ChannelMode.HYBRID_SELF_FIRST,
    region: RegionPreference = RegionPreference.PH_FIRST,
    sku: SkuType = SkuType.CHATGPT_PLUS_1M,
) -> ActivationCode:
    code = ActivationCode(
        code=generate_code(),
        sku_type=sku,
        channel_mode=channel,
        preferred_region=region,
        status=CodeStatus.UNUSED,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def _task(db, code: ActivationCode) -> RechargeTask:
    task = RechargeTask(
        task_id=str(uuid4()),
        code_id=code.id,
        target_email="user@example.com",
        target_user_id="user-1",
        session_kind="chatgpt",
        encrypted_session_raw=encrypt_text(_session().raw),
        state=RechargeState.CHECKING_ACCOUNT,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _card(
    db,
    balance: float = 50,
    *,
    ph: bool = True,
    status: VccStatus = VccStatus.ACTIVE,
    binds: int = 0,
    max_binds: int = 2,
) -> VccCard:
    card = VccCard(
        provider="RDVCC",
        card_bin="424242",
        card_number_enc=encrypt_text("4242424242424242"),
        cvv_enc=encrypt_text("123"),
        exp_month="12",
        exp_year="29",
        holder_name="DEMO USER",
        billing_address_json={
            "line1": "1 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
        },
        ph_billing_address_json=(
            {
                "line1": "1 Ayala Ave",
                "city": "Makati",
                "state": "Metro Manila",
                "postal_code": "1200",
                "country": "PH",
            }
            if ph
            else None
        ),
        three_ds_email_account="3ds@example.com",
        available_balance=Decimal(str(balance)),
        max_bind_count=max_binds,
        current_bind_count=binds,
        failed_count=0,
        status=status,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def _enable_self(db) -> None:
    runtime_settings.update_settings(
        db,
        {"self_vcc_enabled": True, "self_vcc_dry_run": False},
    )


def _fake_self_success(monkeypatch, *, fail_with: SelfVccFailure | None = None):
    monkeypatch.setattr(region_router, "proxy_for", lambda region: "http://127.0.0.1:7890")
    monkeypatch.setattr(region_router, "create_checkout_url", lambda session, region, sku: "https://checkout.test/session")

    def checkout(url, card, proxy, on_state=None):
        if fail_with is not None:
            raise fail_with
        if on_state:
            on_state("AWAITING_3DS")
        return "自营卡池扣款成功，会员已升级"

    monkeypatch.setattr("app.services.stripe_playwright.checkout_and_cancel_autorenew", checkout)


def _fake_upstream(monkeypatch, status: str = "SUCCESS"):
    calls: list[str] = []

    def create_recharge(self, session_raw, sku_type, upstream_code):
        calls.append(sku_type)
        if status == "FAILED":
            return UpstreamResult(status="FAILED", message="上游失败", error_code="UPSTREAM_FAILED")
        return UpstreamResult(status="SUCCESS", order_no="UP-1", message="上游充值成功")

    monkeypatch.setattr(UpstreamProvider, "create_recharge", create_recharge)
    return calls


def _record_feishu(monkeypatch):
    events: list[tuple[str, dict]] = []

    def dispatch_event(event, title, fields, **kwargs):
        events.append((event, fields))

    monkeypatch.setattr(channel_dispatcher.feishu, "dispatch_event", dispatch_event)
    return events


def test_effective_mode_keeps_v1_upstream_and_honours_global_policy(db):
    legacy = _code(db, ChannelMode.UPSTREAM)
    hybrid = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    self_only = _code(db, ChannelMode.SELF_VCC_ONLY)

    assert channel_dispatcher.effective_mode(legacy) == "UPSTREAM_ONLY"
    assert channel_dispatcher.effective_mode(hybrid) == "HYBRID_SELF_FIRST"
    assert channel_dispatcher.effective_mode(self_only) == "SELF_VCC_ONLY"

    runtime_settings.update_settings(db, {"routing_policy": "UPSTREAM_ONLY"})
    assert channel_dispatcher.effective_mode(hybrid) == "UPSTREAM_ONLY"

    runtime_settings.update_settings(db, {"routing_policy": "SELF_VCC_ONLY"})
    assert channel_dispatcher.effective_mode(legacy) == "SELF_VCC_ONLY"


def test_hybrid_prefers_ph_card_then_uses_php_amount(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=True)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "SELF_VCC"
    assert outcome.region == "PH"
    assert outcome.currency == "PHP"
    assert outcome.amount == Decimal("999.0")
    assert upstream_calls == []
    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    card = db.get(VccCard, card.id)
    assert task.executed_channel == "SELF_VCC"
    assert task.executed_region == "PH"
    assert task.billed_currency == "PHP"
    assert card.current_bind_count == 1
    assert card.status == VccStatus.ACTIVE


def test_ph_without_card_falls_to_us_region(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=False)  # US-only card
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.region == "US"
    assert outcome.currency == "USD"
    assert outcome.amount == Decimal("20.0")
    assert upstream_calls == []
    db.expire_all()
    assert db.get(RechargeTask, task.task_id).executed_region == "US"


def test_us_only_region_preference_skips_ph(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    _fake_upstream(monkeypatch)
    ph_card = _card(db, 50, ph=True)
    us_card = _card(db, 60, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.region == "US"
    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    assert task.vcc_card_id == us_card.id
    assert task.vcc_card_id != ph_card.id


def test_self_failure_falls_back_to_upstream_and_records_reason(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure("CHECKOUT_UI_CHANGED", "结账页结构变化", release_code=True),
    )
    upstream_calls = _fake_upstream(monkeypatch)
    events = _record_feishu(monkeypatch)
    _card(db, 50, ph=True)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]
    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    assert task.executed_channel == "UPSTREAM"
    assert task.fallback_reason.startswith("CHECKOUT_UI_CHANGED")
    assert events and events[0][0] == "channel.fallback"


def test_card_decline_suspends_card_before_fallback(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure("CARD_DECLINED", "支付卡被拒绝", release_code=True),
    )
    _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=True)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    db.expire_all()
    card = db.get(VccCard, card.id)
    assert card.status == VccStatus.SUSPENDED
    assert card.failed_count == 1


def test_self_only_failure_does_not_call_upstream(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch, fail_with=SelfVccFailure("CARD_DECLINED", "拒付", release_code=True))
    upstream_calls = _fake_upstream(monkeypatch)
    _card(db, 50, ph=False)
    code = _code(db, ChannelMode.SELF_VCC_ONLY, RegionPreference.US_ONLY)
    task = _task(db, code)

    with pytest.raises(Exception) as exc:
        channel_dispatcher.dispatch(db, task, code, _session())

    assert getattr(exc.value, "code", "") == "CARD_DECLINED"
    assert upstream_calls == []


def test_runtime_upstream_only_policy_overrides_hybrid_code(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=True)
    runtime_settings.update_settings(db, {"routing_policy": "UPSTREAM_ONLY"})
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]
    db.expire_all()
    assert db.get(VccCard, card.id).current_bind_count == 0


def test_v1_upstream_code_still_bypasses_self_pool(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    upstream_calls = _fake_upstream(monkeypatch)
    _card(db, 50, ph=True)
    code = _code(db, ChannelMode.UPSTREAM)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]


def test_dry_run_hybrid_falls_back_without_charging_card(db, monkeypatch):
    runtime_settings.update_settings(
        db,
        {"self_vcc_enabled": True, "self_vcc_dry_run": True},
    )
    _fake_self_success(monkeypatch)
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=True)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]
    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    card = db.get(VccCard, card.id)
    assert task.fallback_reason.startswith("SELF_VCC_DRY_RUN")
    assert card.current_bind_count == 0
    assert card.status == VccStatus.ACTIVE


def test_self_only_dry_run_releases_code(db, monkeypatch):
    runtime_settings.update_settings(
        db,
        {"self_vcc_enabled": True, "self_vcc_dry_run": True},
    )
    upstream_calls = _fake_upstream(monkeypatch)
    code = _code(db, ChannelMode.SELF_VCC_ONLY)
    task = _task(db, code)

    with pytest.raises(Exception) as exc:
        channel_dispatcher.dispatch(db, task, code, _session())

    assert getattr(exc.value, "code", "") == "SELF_VCC_DRY_RUN"
    assert getattr(exc.value, "release_code", False) is True
    assert upstream_calls == []


def test_acquire_card_requires_ph_address_for_ph_region(db):
    us_only = _card(db, 50, ph=False)
    ph_card = _card(db, 40, ph=True)

    selected_ph = vcc_pool_scheduler.acquire_card(db, "PH")
    selected_us = vcc_pool_scheduler.acquire_card(db, "US")

    assert selected_ph.id == ph_card.id
    assert selected_us.id == us_only.id


def test_run_recharge_task_releases_code_on_exhausted_self_only_pool(db, monkeypatch):
    _enable_self(db)
    _fake_upstream(monkeypatch)
    monkeypatch.setattr(region_router, "proxy_for", lambda region: "http://127.0.0.1:7890")
    code = _code(db, ChannelMode.SELF_VCC_ONLY)
    task = _task(db, code)

    run_recharge_task(task.task_id)

    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    code = db.get(ActivationCode, code.id)
    assert task.state == RechargeState.FAILED
    assert task.error_code == "NO_ACTIVE_VCC_IN_POOL"
    assert code.status == CodeStatus.UNUSED


def test_admin_card_ph_address_batch_import_and_mark_closed(client, admin_headers, db):
    payload = {
        "provider": "RDVCC",
        "card_number": "4242424242424242",
        "cvv": "123",
        "exp_month": "12",
        "exp_year": "2029",
        "holder_name": "DEMO USER",
        "billing_address": {
            "line1": "1 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
        },
        "ph_billing_address": {
            "line1": "1 Ayala Ave",
            "city": "Makati",
            "state": "Metro Manila",
            "postal_code": "1200",
            "country": "PH",
        },
        "three_ds_email_account": "3ds@example.com",
        "available_balance": 30,
        "max_bind_count": 2,
    }
    created = client.post("/api/v1/admin/cards", json=payload, headers=admin_headers)
    assert created.status_code == 200, created.text

    batch = client.post(
        "/api/v1/admin/cards/batch-import",
        json={"cards": [payload, payload]},
        headers=admin_headers,
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["created"] == 2

    listing = client.get("/api/v1/admin/cards", headers=admin_headers)
    assert listing.status_code == 200
    item = next(row for row in listing.json()["items"] if row["id"] == created.json()["id"])
    assert item["has_ph_billing"] is True
    assert item["three_ds_email_masked"]

    summary = client.get("/api/v1/admin/cards/summary", headers=admin_headers)
    assert summary.status_code == 200
    assert summary.json()["routing_policy"] in {"HYBRID_SELF_FIRST", "SELF_VCC_ONLY", "UPSTREAM_ONLY"}

    closed = client.post(
        f"/api/v1/admin/cards/{created.json()['id']}/mark-closed",
        headers=admin_headers,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"

    policy = client.patch(
        "/api/v1/admin/routing-policy",
        json={"routing_policy": "UPSTREAM_ONLY"},
        headers=admin_headers,
    )
    assert policy.status_code == 200
    assert policy.json()["routing_policy"] == "UPSTREAM_ONLY"
    runtime_settings.refresh()
    assert runtime_settings.get("routing_policy") == "UPSTREAM_ONLY"


def test_admin_code_generation_accepts_new_channel_and_region(client, admin_headers):
    response = client.post(
        "/api/v1/admin/codes/generate",
        json={
            "count": 1,
            "sku_type": "CHATGPT_PLUS_1M",
            "channel_mode": "HYBRID_SELF_FIRST",
            "preferred_region": "US_ONLY",
            "note": "v2-test",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    listing = client.get(
        "/api/v1/admin/codes?status=ALL&sku_type=ALL&page=1&page_size=100",
        headers=admin_headers,
    )
    assert listing.status_code == 200
    match = next(
        row
        for row in listing.json()["items"]
        if row["channel_mode"] == "HYBRID_SELF_FIRST"
        and row["preferred_region"] == "US_ONLY"
    )
    assert match["note"] == "v2-test"
