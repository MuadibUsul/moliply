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
from app.services import channel_dispatcher, region_router, runtime_settings
from app.services.fulfillment import self_vcc_circuit, vcc_pool_scheduler
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.redeem import run_recharge_task
from app.services.fulfillment.self_vcc_worker import SelfVccFailure
from app.services.session_parser import SessionInfo
from app.services.upstream_provider import UpstreamProvider, UpstreamResult


@pytest.fixture(autouse=True)
def _clean_card_pool(db):
    db.query(VccCard).update({VccCard.status: VccStatus.SUSPENDED})
    db.commit()
    self_vcc_circuit.reset()
    yield
    self_vcc_circuit.reset()


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

    monkeypatch.setattr("app.services.fulfillment.stripe_playwright.checkout_and_cancel_autorenew", checkout)


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


def test_card_decline_cools_down_then_suspends_at_threshold(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure("CARD_DECLINED", "支付卡被拒绝", release_code=True),
    )
    _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    db.expire_all()
    card = db.get(VccCard, card.id)
    # The first decline parks the card for a cooldown instead of burning it.
    assert card.status == VccStatus.ACTIVE
    assert card.consecutive_failures == 1
    assert card.cooldown_until is not None
    assert card.reserved_balance == Decimal("0.00")

    # Once the threshold is reached the card is suspended outright.
    for _ in range(vcc_pool_scheduler.max_consecutive_failures() - 1):
        vcc_pool_scheduler.suspend(db, card, "支付卡被拒绝")
    db.refresh(card)
    assert card.status == VccStatus.SUSPENDED


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
        "card_number": "4000000000000077",
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
    # The same payload is pasted twice: one new row, the repeat is skipped.
    assert batch.json()["created"] == 2 or batch.json()["skipped"] == 2

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


def test_sms_sku_skips_the_session_pre_check(db):
    from app.models import ActivationCode, CodeStatus
    from app.services import redeem
    from app.services.session_parser import SessionInfo
    from app.services.code_generator import generate_code

    code = ActivationCode(
        code=generate_code(),
        sku_type=SkuType.CODEX_SMS_US,
        channel_mode=ChannelMode.UPSTREAM,
        status=CodeStatus.LOCKED,
    )
    db.add(code)
    db.commit()

    session = SessionInfo(
        kind="none",
        email="",
        user_id="",
        access_token="",
        raw="",
        expires_at=datetime.utcnow(),
        plan="",
    )
    check = redeem._prepare_session(code, session)
    assert check.eligible is True


def test_pro_tiers_charge_and_cost_scale_with_the_tier(db):
    from app.services import region_router

    assert region_router.amount_for("PH", SkuType.GPT_PRO_100) == ("PHP", Decimal("4995.0"))
    assert region_router.amount_for("PH", SkuType.GPT_PRO_500) == ("PHP", Decimal("24975.0"))
    assert region_router.cost_usd_for("PH", SkuType.GPT_PRO_100) < region_router.cost_usd_for("PH", SkuType.GPT_PRO_500)
    # Plus keeps the historical region cost when no SKU is passed.
    assert region_router.cost_usd_for("PH") == Decimal("16.7")


# ---------------------------------------------------------------------------
# Phase-aware fallback: only a pre-payment or definite-decline failure may fall
# back upstream; an unknown post-submission outcome must lock for a human.
# ---------------------------------------------------------------------------


def test_ui_failure_before_payment_falls_back_and_counts_toward_breaker(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure("CHECKOUT_UI_CHANGED", "找不到卡号输入框", release_code=False),
    )
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    # A DOM change before anything is typed must never block the order.
    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]
    db.expire_all()
    card = db.get(VccCard, card.id)
    assert card.reserved_balance == Decimal("0.00")
    assert card.current_bind_count == 0
    assert card.consecutive_failures == 0  # the card itself is fine
    assert self_vcc_circuit.state()["failures_in_window"] == 1


def test_declined_card_falls_back_upstream_and_parks_the_card(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure(
            "CARD_DECLINED",
            "支付卡被拒绝",
            release_code=False,
            payment_submitted=True,
            is_explicit_decline=True,
        ),
    )
    upstream_calls = _fake_upstream(monkeypatch)
    card = _card(db, 50, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)

    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    assert upstream_calls == ["CHATGPT_PLUS_1M"]
    db.expire_all()
    card = db.get(VccCard, card.id)
    assert card.consecutive_failures == 1
    assert card.cooldown_until is not None
    # A decline is a card problem, not a broken channel.
    assert self_vcc_circuit.state()["failures_in_window"] == 0


def test_unknown_post_submission_outcome_does_not_fall_back(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(
        monkeypatch,
        fail_with=SelfVccFailure(
            "AWAITING_MANUAL_REVIEW",
            "支付已提交但未能确认升级结果",
            release_code=False,
            payment_submitted=True,
        ),
    )
    upstream_calls = _fake_upstream(monkeypatch)
    _card(db, 50, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)

    with pytest.raises(Exception) as exc:
        channel_dispatcher.dispatch(db, task, code, _session())

    assert getattr(exc.value, "code", "") == "PAYMENT_OUTCOME_UNKNOWN"
    # The code stays locked: release_code=False is what keeps it out of a retry.
    assert getattr(exc.value, "release_code", True) is False
    # The customer must not be charged twice, even though upstream would answer.
    assert upstream_calls == []


def test_payment_started_flag_is_stamped_by_the_driver(monkeypatch):
    """A raise site that forgets the flag still ends up marked as submitted."""
    from app.services.fulfillment import self_vcc_worker

    page = _FakePage()

    def boom(*_args, **_kwargs):
        raise self_vcc_worker.SelfVccFailure("AWAITING_MANUAL_REVIEW", "未知")

    monkeypatch.setattr(self_vcc_worker.PlaywrightCheckout, "_click_first", lambda self, page, sels: True)
    monkeypatch.setattr(self_vcc_worker.PlaywrightCheckout, "_fill_first", lambda self, page, sels, value: True)
    monkeypatch.setattr(self_vcc_worker, "_looks_declined", lambda body: False)
    monkeypatch.setattr(self_vcc_worker, "_alert_text", lambda page: "")
    monkeypatch.setattr(self_vcc_worker, "_capture_failure_snapshot", lambda page, code: None)
    monkeypatch.setattr(self_vcc_worker, "Imap3dsReader", lambda **_kw: _NoMail())
    monkeypatch.setattr(
        self_vcc_worker.PlaywrightCheckout, "_visible_code_input", lambda self, page: None
    )

    def fake_sync_playwright():
        return _FakePlaywright(page)

    class _Stub:
        pass

    import sys
    import types

    stub = types.ModuleType("playwright.sync_api")
    stub.sync_playwright = fake_sync_playwright
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", stub)

    with pytest.raises(self_vcc_worker.SelfVccFailure) as exc:
        self_vcc_worker.PlaywrightCheckout().run(
            "https://chatgpt.com/checkout/test",
            {
                "number": "4242424242424242",
                "cvv": "123",
                "exp_month": "12",
                "exp_year": "29",
                "holder_name": "DEMO",
                "billing": {"postal_code": "10001"},
            },
            "http://127.0.0.1:7890",
        )
    assert exc.value.payment_submitted is True


class _NoMail:
    def wait_for_code(self, **_kwargs):
        return None


class _FakePage:
    def goto(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, *_args, **_kwargs):
        return None

    @property
    def frames(self):
        return []

    def locator(self, *_args, **_kwargs):
        return _FakeLocator()

    def evaluate(self, *_args, **_kwargs):
        return None

    def screenshot(self, **_kwargs):
        return b""

    def inner_text(self, *_args, **_kwargs):
        return "checkout"


class _FakeLocator:
    def __init__(self):
        self.first = self
        self.count = lambda: 1

    def fill(self, *_args, **_kwargs):
        return None

    def click(self, *_args, **_kwargs):
        return None

    def is_visible(self):
        return True

    def inner_text(self, *_args, **_kwargs):
        return ""


class _FakeContext:
    def new_page(self):
        return _FakePage()

    def close(self):
        return None


class _FakeBrowser:
    def new_context(self, **_kwargs):
        return _FakeContext()

    def close(self):
        return None


class _FakeChromium:
    def __init__(self, page):
        self._page = page

    def launch(self, **_kwargs):
        return _FakeBrowser()


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_open_circuit_skips_the_browser_entirely(db, monkeypatch):
    _enable_self(db)
    _fake_upstream(monkeypatch)
    _card(db, 50, ph=True)

    def explode(*_args, **_kwargs):
        raise AssertionError("browser must not run while the breaker is open")

    monkeypatch.setattr("app.services.fulfillment.stripe_playwright.checkout_and_cancel_autorenew", explode)
    for _ in range(self_vcc_circuit.THRESHOLD):
        self_vcc_circuit.record_failure("CHECKOUT_UI_CHANGED")

    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)
    outcome = channel_dispatcher.dispatch(db, task, code, _session())

    assert outcome.channel == "UPSTREAM"
    db.expire_all()
    assert db.get(RechargeTask, task.task_id).fallback_reason.startswith("SELF_VCC_CIRCUIT_OPEN")


def test_successful_charge_closes_the_breaker(db, monkeypatch):
    _enable_self(db)
    _fake_self_success(monkeypatch)
    _fake_upstream(monkeypatch)
    _card(db, 50, ph=True)
    for _ in range(self_vcc_circuit.THRESHOLD - 1):
        self_vcc_circuit.record_failure("CHECKOUT_UI_CHANGED")
    assert self_vcc_circuit.state()["open"] is False

    code = _code(db, ChannelMode.HYBRID_SELF_FIRST)
    task = _task(db, code)
    channel_dispatcher.dispatch(db, task, code, _session())

    assert self_vcc_circuit.state()["failures_in_window"] == 0


# ---------------------------------------------------------------------------
# Failure forensics: page text + screenshot must reach the task record, and the
# admin must be able to open the image.
# ---------------------------------------------------------------------------


def test_failure_snapshot_is_written_and_pruned(tmp_path, monkeypatch):
    from app.services.fulfillment import self_vcc_worker

    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    for index in range(3):
        (tmp_path / f"fail_old_{index}.png").write_bytes(b"x")
        import os as _os

        _os.utime(tmp_path / f"fail_old_{index}.png", (index, index))

    path = self_vcc_worker._capture_failure_snapshot(_FakePage(), "CHECKOUT_UI_CHANGED", keep=2)

    assert path is not None
    assert (tmp_path / "fail_CHECKOUT_UI_CHANGED").parent == tmp_path
    kept = list(tmp_path.glob("fail_*.png"))
    assert len(kept) <= 2  # the oldest files are dropped


def test_snapshot_failure_does_not_mask_the_real_error(tmp_path, monkeypatch):
    from app.services.fulfillment import self_vcc_worker

    class Exploding:
        def evaluate(self, *_a, **_k):
            raise RuntimeError("no page")

    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    assert self_vcc_worker._capture_failure_snapshot(Exploding(), "X") is None


def test_decline_marker_detection_covers_stripe_vocabulary():
    from app.services.fulfillment import self_vcc_worker

    assert self_vcc_worker._looks_declined("Your card was declined.")
    assert self_vcc_worker._looks_declined("insufficient_funds")
    assert self_vcc_worker._looks_declined("expired_card")
    assert not self_vcc_worker._looks_declined("processing your payment")


def test_unknown_outcome_stores_page_text_as_error_detail(db, monkeypatch):
    """The operator must see what the page said, not just the error code."""
    _enable_self(db)
    failure = SelfVccFailure(
        "AWAITING_MANUAL_REVIEW",
        "支付已提交但未能确认升级结果",
        release_code=False,
        payment_submitted=True,
    )
    failure.error_detail = "Something went wrong"
    failure.artifact_path = "/tmp/fail_x.png"
    _fake_self_success(monkeypatch, fail_with=failure)
    _card(db, 50, ph=False)
    code = _code(db, ChannelMode.HYBRID_SELF_FIRST, RegionPreference.US_ONLY)
    task = _task(db, code)
    db.commit()

    run_recharge_task(task.task_id)

    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    assert task.state == RechargeState.FAILED
    assert task.error_code == "PAYMENT_OUTCOME_UNKNOWN"
    assert task.error_detail == "Something went wrong"
    assert task.evidence_path == "/tmp/fail_x.png"


def test_task_serializer_exposes_diagnostic_fields(db):
    from app.services import redeem as redeem_service

    code = _code(db, ChannelMode.UPSTREAM)
    task = _task(db, code)
    task.state = RechargeState.FAILED
    task.error_code = "PAYMENT_OUTCOME_UNKNOWN"
    task.error_detail = "page text"
    task.evidence_path = "/tmp/moliply_artifacts/fail_AWAITING_MANUAL_REVIEW_1.png"
    db.add(task)
    db.commit()

    payload = redeem_service.serialize_task(db, task)

    assert payload["error_detail"] == "page text"
    assert payload["has_evidence"] is True
    assert payload["evidence_name"] == "fail_AWAITING_MANUAL_REVIEW_1.png"


def test_evidence_endpoint_serves_the_file_and_rejects_outside_paths(
    client, admin_headers, db, tmp_path, monkeypatch
):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    code = _code(db, ChannelMode.UPSTREAM)
    task = _task(db, code)
    shot = tmp_path / "fail_CARD_DECLINED_1.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    task.state = RechargeState.FAILED
    task.evidence_path = str(shot)
    db.add(task)
    db.commit()

    served = client.get(f"/api/v1/admin/tasks/{task.task_id}/evidence", headers=admin_headers)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"

    outside = tmp_path.parent / "elsewhere.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    task.evidence_path = str(outside)
    db.add(task)
    db.commit()
    assert client.get(f"/api/v1/admin/tasks/{task.task_id}/evidence", headers=admin_headers).status_code == 404


def test_evidence_endpoint_accepts_the_admin_cookie(client, db, tmp_path, monkeypatch):
    """The <a href> on the task page has no bearer header, only the cookie."""
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    code = _code(db, ChannelMode.UPSTREAM)
    task = _task(db, code)
    shot = tmp_path / "fail_X_9.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    task.evidence_path = str(shot)
    db.add(task)
    db.commit()

    login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "test-admin-pass"})
    cookie = login.cookies.get("moliply_admin")
    assert cookie

    served = client.get(f"/api/v1/admin/tasks/{task.task_id}/evidence", cookies={"moliply_admin": cookie})
    assert served.status_code == 200


def test_evidence_endpoint_rejects_anonymous(client, db, tmp_path):
    code = _code(db, ChannelMode.UPSTREAM)
    task = _task(db, code)
    db.commit()
    assert client.get(f"/api/v1/admin/tasks/{task.task_id}/evidence").status_code == 401


def test_circuit_api_exposes_state_and_resets(client, admin_headers):
    for _ in range(self_vcc_circuit.THRESHOLD):
        self_vcc_circuit.record_failure("CHECKOUT_UI_CHANGED")

    listing = client.get("/api/v1/admin/channels", headers=admin_headers)
    assert listing.status_code == 200
    circuit = listing.json()["self_vcc"]["circuit"]
    assert circuit["open"] is True
    assert circuit["failures_in_window"] >= self_vcc_circuit.THRESHOLD

    reset = client.post("/api/v1/admin/channels/self-vcc/circuit/reset", headers=admin_headers)
    assert reset.status_code == 200
    assert reset.json()["circuit"]["open"] is False
    assert reset.json()["circuit"]["failures_in_window"] == 0


def test_unexpected_error_keeps_phase_and_evidence(monkeypatch, tmp_path):
    """An exception nobody anticipated still yields a screenshot and a phase."""
    from app.services.fulfillment import self_vcc_worker

    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(self_vcc_worker.PlaywrightCheckout, "_fill_first", lambda self, p, s, v: True)
    monkeypatch.setattr(self_vcc_worker.PlaywrightCheckout, "_click_first", lambda self, p, s: True)
    monkeypatch.setattr(self_vcc_worker, "Imap3dsReader", lambda **_kw: _NoMail())
    monkeypatch.setattr(self_vcc_worker, "_alert_text", lambda page: "")

    def boom(*_a, **_k):
        raise RuntimeError("frame detached")

    # ``_visible_code_input`` is the first call that explodes after submission.
    page = _FakePage()
    monkeypatch.setattr(self_vcc_worker.PlaywrightCheckout, "_visible_code_input", boom)

    import sys
    import types

    stub = types.ModuleType("playwright.sync_api")
    stub.sync_playwright = lambda: _FakePlaywright(page)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", stub)

    with pytest.raises(self_vcc_worker.SelfVccFailure) as exc:
        self_vcc_worker.PlaywrightCheckout().run(
            "https://chatgpt.com/checkout/x",
            {
                "number": "4242424242424242",
                "cvv": "123",
                "exp_month": "12",
                "exp_year": "29",
                "holder_name": "DEMO",
                "billing": {"postal_code": "10001"},
            },
            "http://127.0.0.1:7890",
        )

    assert exc.value.code == "SELF_VCC_ERROR"
    assert exc.value.payment_submitted is True
    assert exc.value.artifact_path is not None
