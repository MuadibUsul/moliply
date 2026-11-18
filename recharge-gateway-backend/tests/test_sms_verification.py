"""SMS receipt: OTP consumption and free number change.

The upstream is faked at the provider boundary, so the state machine — not the
HTTP plumbing — is what these tests exercise. The two paths that must not
regress are the only ones that touch money:

* an OTP arriving consumes the code exactly once;
* no OTP inside the window refunds upstream and gives the code back.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    Product,
    ProductCategory,
    DeliveryType,
    SmsTaskStatus,
    SmsVerificationTask,
    SkuType,
    utcnow,
)
from app.services import sms as sms_service
from app.services.code_generator import generate_code
from app.services.sms import providers


class FakeProvider:
    """Stand-in upstream: a queue of poll answers plus a cancel counter."""

    name = "smspool"

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.cancelled: list[str] = []
        self.leased: list[tuple[str, str]] = []
        self.counter = 0

    def configured(self) -> bool:
        return True

    def lease_number(self, service: str, country: str):
        self.leased.append((service, country))
        self.counter += 1
        return providers.LeasedNumber(
            upstream_order_id=str(self.counter),
            phone_number=f"+1415555{self.counter:04d}",
            provider=self.name,
        )

    def poll_code(self, lease):
        if not self.answers:
            return providers.ReceivedSms(False)
        return self.answers.pop(0)

    def cancel(self, lease) -> bool:
        self.cancelled.append(lease.upstream_order_id)
        return True


@pytest.fixture()
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(providers, "choose_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(providers, "build_provider", lambda _name: provider)
    monkeypatch.setattr(providers, "available_providers", lambda: ["smspool"])
    return provider


def _sms_product(db) -> Product:
    product = Product(
        slug=f"sms-probe-{utcnow().timestamp()}",
        name="SMS Probe",
        subtitle="probe",
        category=ProductCategory.DIGITAL,
        brand="MOLIPLY",
        logo_key="default",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price=Decimal("8.80"),
        active=True,
    )
    db.add(product)
    db.commit()
    return product


def _code(db, status=CodeStatus.UNUSED) -> ActivationCode:
    code = ActivationCode(
        code=generate_code(),
        sku_type=SkuType.SMS_VERIFY,
        channel_mode=ChannelMode.UPSTREAM,
        status=status,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


# ---------------------------------------------------------------------------
# Leasing holds the code, never consumes it
# ---------------------------------------------------------------------------


def test_leasing_holds_the_code_and_exposes_the_number(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")

    db.expire_all()
    code = db.get(ActivationCode, code.id)
    assert code.status is CodeStatus.LOCKED_WAITING_OTP
    assert code.status is not CodeStatus.USED
    assert task.phone_number.startswith("+1")
    assert task.status is SmsTaskStatus.WAITING
    assert fake.leased == [("claudeai", "US")]


def test_leasing_twice_returns_the_same_live_task(db, fake):
    code = _code(db)
    first = sms_service.lease(db, code, "claude", "US")
    second = sms_service.lease(db, code, "claude", "US")
    assert first.task_id == second.task_id
    assert len(fake.leased) == 1


def test_poll_before_otp_keeps_the_code_held(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    fake.answers = [providers.ReceivedSms(False)]

    result = sms_service.poll(db, task.task_id)

    db.expire_all()
    assert result.status is SmsTaskStatus.WAITING
    assert db.get(ActivationCode, code.id).status is CodeStatus.LOCKED_WAITING_OTP


# ---------------------------------------------------------------------------
# Path 1: an OTP arrives -> the code is consumed exactly once
# ---------------------------------------------------------------------------


def test_otp_consumes_the_code_and_records_the_message(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    fake.answers = [
        providers.ReceivedSms(True, "482913", "Your Claude code is 482913. Do not share it.")
    ]

    result = sms_service.poll(db, task.task_id)

    db.expire_all()
    code = db.get(ActivationCode, code.id)
    assert result.status is SmsTaskStatus.RECEIVED
    assert result.otp_code == "482913"
    assert "482913" in (result.raw_sms or "")
    assert code.status is CodeStatus.USED
    assert code.used_at is not None


def test_further_polls_after_success_do_not_reconsume(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    fake.answers = [providers.ReceivedSms(True, "551177", "code 551177")]
    sms_service.poll(db, task.task_id)
    fake.answers = []

    sms_service.poll(db, task.task_id)
    db.expire_all()
    assert db.get(ActivationCode, code.id).status is CodeStatus.USED
    assert len(fake.cancelled) == 0


def test_successful_receipt_does_not_cancel_upstream(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    fake.answers = [providers.ReceivedSms(True, "111222", "code 111222")]
    sms_service.poll(db, task.task_id)
    assert fake.cancelled == []


# ---------------------------------------------------------------------------
# Path 2: 120 s with nothing -> refund upstream and release the code
# ---------------------------------------------------------------------------


def test_expiry_cancels_upstream_and_restores_the_code(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")

    # Push the deadline into the past instead of sleeping 120 s.
    task = db.get(SmsVerificationTask, task.task_id)
    task.expires_at = utcnow() - timedelta(seconds=1)
    db.add(task)
    db.commit()

    result = sms_service.poll(db, task.task_id)

    db.expire_all()
    code = db.get(ActivationCode, code.id)
    assert result.status is SmsTaskStatus.EXPIRED
    assert code.status is CodeStatus.UNUSED
    assert fake.cancelled == [task.upstream_order_id]


def test_expired_code_can_lease_a_new_number(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    db.query(SmsVerificationTask).filter(SmsVerificationTask.task_id == task.task_id).update(
        {SmsVerificationTask.expires_at: utcnow() - timedelta(seconds=1)}
    )
    db.commit()
    sms_service.poll(db, task.task_id)

    fresh = sms_service.lease(db, code, "claude", "US")
    assert fresh.task_id != task.task_id
    assert fresh.phone_number != task.phone_number
    db.expire_all()
    assert db.get(ActivationCode, code.id).status is CodeStatus.LOCKED_WAITING_OTP


def test_sweep_expires_overdue_tasks(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    db.query(SmsVerificationTask).filter(SmsVerificationTask.task_id == task.task_id).update(
        {SmsVerificationTask.expires_at: utcnow() - timedelta(seconds=5)}
    )
    db.commit()

    assert sms_service.sweep_expired(db) == 1
    db.expire_all()
    assert db.get(ActivationCode, code.id).status is CodeStatus.UNUSED
    assert fake.cancelled == [task.upstream_order_id]


# ---------------------------------------------------------------------------
# Customer-requested number change
# ---------------------------------------------------------------------------


def test_change_number_refunds_and_leases_a_new_one(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")

    changed = sms_service.change_number(db, task.task_id)

    db.expire_all()
    assert changed.task_id != task.task_id
    assert db.get(ActivationCode, code.id).status is CodeStatus.LOCKED_WAITING_OTP
    assert db.get(SmsVerificationTask, task.task_id).status is SmsTaskStatus.CANCELLED
    assert fake.cancelled == [task.upstream_order_id]


def test_change_after_receipt_is_a_no_op(db, fake):
    code = _code(db)
    task = sms_service.lease(db, code, "claude", "US")
    fake.answers = [providers.ReceivedSms(True, "998877", "code 998877")]
    sms_service.poll(db, task.task_id)

    same = sms_service.change_number(db, task.task_id)
    assert same.task_id == task.task_id
    assert fake.cancelled == []


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------


def test_dry_region_releases_the_hold_and_keeps_the_code(db, monkeypatch):
    code = _code(db)
    # A failed lease may have left the code held by an earlier attempt.
    code.status = CodeStatus.LOCKED_WAITING_OTP
    db.add(code)
    db.commit()

    class Dry(FakeProvider):
        def lease_number(self, service, country):
            raise providers.NoNumbersAvailable()

    monkeypatch.setattr(providers, "choose_provider", lambda *_a, **_k: Dry())
    with pytest.raises(sms_service.SmsError) as exc:
        sms_service.lease(db, code, "claude", "GB")

    db.expire_all()
    assert exc.value.code == "NO_NUMBERS"
    assert db.get(ActivationCode, code.id).status is CodeStatus.UNUSED


def test_otp_extraction_ignores_amounts_and_short_numbers():
    assert providers.extract_otp("Total $12. Your code: 448219") == "448219"
    assert providers.extract_otp("Code 90210 valid for 10 minutes") == "90210"
    assert providers.extract_otp("no digits here") is None
    assert providers.extract_otp("Your code is 2026") == "2026"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_http_lease_poll_and_change(client, db, fake):
    code = _code(db)
    leased = client.post(
        "/api/v1/sms/lease",
        json={"code": code.code, "service": "claude", "country": "US"},
    )
    assert leased.status_code == 200, leased.text
    task_id = leased.json()["task"]["task_id"]
    assert leased.json()["task"]["phone_number"]

    fake.answers = [providers.ReceivedSms(True, "314159", "ChatGPT code 314159")]
    polled = client.get(f"/api/v1/sms/tasks/{task_id}")
    assert polled.status_code == 200
    assert polled.json()["task"]["otp_code"] == "314159"
    assert polled.json()["task"]["status"] == "RECEIVED"

    db.expire_all()
    assert db.get(ActivationCode, code.id).status is CodeStatus.USED


def test_http_change_button_appears_after_45_seconds(client, db, fake):
    code = _code(db)
    leased = client.post("/api/v1/sms/lease", json={"code": code.code, "service": "google", "country": "US"})
    task_id = leased.json()["task"]["task_id"]
    assert leased.json()["task"]["can_change_number"] is False

    db.query(SmsVerificationTask).filter(SmsVerificationTask.task_id == task_id).update(
        {SmsVerificationTask.expires_at: utcnow() + timedelta(seconds=60)}
    )
    db.commit()
    fake.answers = [providers.ReceivedSms(False)]

    payload = client.get(f"/api/v1/sms/tasks/{task_id}").json()["task"]
    assert payload["can_change_number"] is True


def test_http_options_lists_services_and_countries(client, fake):
    payload = client.get("/api/v1/sms/options").json()
    assert {item["key"] for item in payload["services"]} >= {"claude", "chatgpt", "google"}
    assert payload["countries"][0]["key"] == "US"
    assert payload["provider_ready"] is True


def test_http_lease_rejects_a_non_sms_code(client, db, fake):
    code = _code(db)
    code.sku_type = SkuType.GENERIC_DIGITAL
    db.add(code)
    db.commit()

    response = client.post("/api/v1/sms/lease", json={"code": code.code, "service": "claude", "country": "US"})
    assert response.status_code == 409
    assert response.json()["code"] == "NOT_SMS_CODE"


def test_verify_code_reports_the_sms_sku_and_keeps_a_held_code_usable(client, db, fake):
    code = _code(db)
    sms_service.lease(db, code, "claude", "US")

    payload = client.post("/api/v1/redeem/verify-code", json={"code": code.code}).json()
    assert payload["sms"] is True
    assert payload["requires_session"] is False
    assert payload["redeemable"] is True
    assert payload["status"] == "LOCKED_WAITING_OTP"


def test_resuming_a_held_code_returns_the_same_number(db, fake):
    code = _code(db)
    first = sms_service.lease(db, code, "claude", "US")
    again = sms_service.lease(db, code, "claude", "US")
    assert again.task_id == first.task_id
    assert again.phone_number == first.phone_number
    assert len(fake.leased) == 1


def test_celery_worker_registers_the_sms_sweep():
    """A broken decorator line once shipped to prod; import the module for real."""
    import app.tasks.celery_worker as worker

    assert "app.tasks.celery_worker.sweep_sms_tasks_task" in worker.celery_app.tasks
