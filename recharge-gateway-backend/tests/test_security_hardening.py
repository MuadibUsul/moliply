"""Security regression tests for the hardening pass."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pyotp

from app.config import settings
from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    Order,
    OrderStatus,
    PaymentEvent,
    RechargeState,
    RechargeTask,
    SkuType,
    VccCard,
    VccStatus,
)
from app.services import orders as order_service
from app.services import runtime_settings, vcc_pool_scheduler
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.payments.base import ParsedNotify
from app.services.payments.guards import mock_payments_allowed
from app.services.redeem import retry_task, run_recharge_task
from app.security import admin_login_guard


@pytest.fixture(autouse=True)
def _suspend_cards(db):
    db.query(VccCard).update({VccCard.status: VccStatus.SUSPENDED})
    db.commit()
    yield


def _card(db, *, balance=50, ph=True, max_binds=1):
    card = VccCard(
        provider="RDVCC",
        card_bin="424242",
        card_number_enc=encrypt_text("4242424242424242"),
        cvv_enc=encrypt_text("123"),
        exp_month="12",
        exp_year="29",
        holder_name="DEMO USER",
        billing_address_json={"line1": "1 Main St", "city": "New York", "state": "NY", "postal_code": "10001", "country": "US"},
        ph_billing_address_json=(
            {"line1": "1 Ayala Ave", "city": "Makati", "state": "Metro Manila", "postal_code": "1200", "country": "PH"}
            if ph
            else None
        ),
        available_balance=Decimal(str(balance)),
        max_bind_count=max_binds,
        current_bind_count=0,
        failed_count=0,
        status=VccStatus.ACTIVE,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def test_mock_gate_blocks_after_runtime_switch_off(client, db):
    order = _create_order(client, db)
    runtime_settings.update_settings(db, {"payment_mode": "mock", "mock_payment_enabled": False})
    assert mock_payments_allowed() is False
    with pytest.raises(order_service.OrderError) as exc:
        order_service.settle_mock_order(db, order)
    assert exc.value.code == "MOCK_PAYMENT_DISABLED"


def test_mock_gate_production_requires_explicit_opt_in(db, monkeypatch):
    runtime_settings.update_settings(db, {"payment_mode": "mock", "mock_payment_enabled": True})
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_mock_payment_in_production", False)
    assert mock_payments_allowed() is False
    monkeypatch.setattr(settings, "allow_mock_payment_in_production", True)
    assert mock_payments_allowed() is True


def _create_order(client, db):
    from app.models import Product

    product = db.query(Product).filter(Product.active.is_(True)).first()
    assert product
    response = client.post(
        "/api/v1/store/orders",
        json={
            "email": f"audit-{uuid4().hex[:8]}@example.com",
            "password": "StrongPass!2026",
            "items": [{"product_id": product.id, "quantity": 1}],
            "payment_method": "wechat",
        },
    )
    assert response.status_code == 201, response.text
    order_no = response.json()["order"]["order_no"]
    db.expire_all()
    return db.query(Order).filter(Order.order_no == order_no).one()


def _login(client, password, code=""):
    return client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": password, "totp_code": code},
    )


def test_admin_login_requires_totp_when_configured(client, monkeypatch):
    secret = pyotp.random_base32()
    monkeypatch.setattr(settings, "admin_totp_secret", secret)
    admin_login_guard.reset()

    without = _login(client, "test-admin-pass")
    assert without.status_code == 401
    wrong = _login(client, "test-admin-pass", "000000")
    assert wrong.status_code == 401
    valid = _login(client, "test-admin-pass", pyotp.TOTP(secret).now())
    assert valid.status_code == 200, valid.text


def test_admin_login_lockout_and_security_alert(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_failed_login_limit", 3)
    monkeypatch.setattr(settings, "admin_login_lockout_minutes", 15)
    admin_login_guard.reset()
    alerts = []
    monkeypatch.setattr(
        "app.api.admin_manage.feishu.dispatch_security_alert",
        lambda title, fields, severity="error": alerts.append((title, fields)),
    )

    codes = [_login(client, "wrong-password").status_code for _ in range(3)]
    assert codes == [401, 401, 429]
    assert alerts and "来源 IP" in alerts[0][1]
    blocked = _login(client, "test-admin-pass")
    assert blocked.status_code == 429
    admin_login_guard.reset()


def test_pending_event_can_be_upgraded_to_success(client, db, monkeypatch):
    order = _create_order(client, db)

    class FakeProvider:
        def parse_notify(self, payload, signature=None):
            return ParsedNotify(
                provider="fake",
                order_no=payload["order_no"],
                amount=Decimal(str(payload["amount"])),
                trade_no=payload["trade_id"],
                verified=True,
                success=payload["status"] == "finished",
                raw={"status": payload["status"]},
            )

        def success_body(self):
            return "OK"

    monkeypatch.setattr(order_service, "get_callback_provider", lambda name: FakeProvider())
    waiting = {
        "order_no": order.order_no,
        "amount": str(order.amount),
        "trade_id": "pay-1",
        "status": "waiting",
    }
    assert order_service.process_payment_notify(db, "fake", waiting) == "OK"
    db.expire_all()
    order = db.query(Order).filter(Order.order_no == order.order_no).one()
    assert order.status == OrderStatus.PENDING
    event = db.query(PaymentEvent).filter(PaymentEvent.event_id == "pay-1").one()
    assert event.raw_json["success"] is False

    finished = dict(waiting, status="finished")
    assert order_service.process_payment_notify(db, "fake", finished) == "OK"
    db.expire_all()
    order = db.query(Order).filter(Order.order_no == order.order_no).one()
    assert order.status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    event = db.query(PaymentEvent).filter(PaymentEvent.event_id == "pay-1").one()
    assert event.raw_json["success"] is True
    # Replaying the final notification must stay idempotent.
    assert order_service.process_payment_notify(db, "fake", finished) == "OK"


def test_payment_amount_is_checked_against_stored_invoice_amount(client, db, monkeypatch):
    order = _create_order(client, db)
    order.payment_amount = Decimal("21.95")
    order.payment_currency = "USD"
    db.add(order)
    db.commit()

    class FakeProvider:
        def parse_notify(self, payload, signature=None):
            return ParsedNotify(
                provider="fake",
                order_no=payload["order_no"],
                amount=Decimal(str(payload["amount"])),
                trade_no="pay-amount",
                verified=True,
                success=True,
                currency="USD",
                raw={"status": "finished"},
            )

        def success_body(self):
            return "OK"

    monkeypatch.setattr(order_service, "get_callback_provider", lambda name: FakeProvider())
    with pytest.raises(order_service.OrderError) as exc:
        order_service.process_payment_notify(
            db,
            "fake",
            {"order_no": order.order_no, "amount": "1.00"},
        )
    assert exc.value.code == "PAYMENT_AMOUNT_MISMATCH"


def test_card_reservation_prevents_concurrent_over_bind(db):
    card = _card(db, max_binds=1)
    first = vcc_pool_scheduler.acquire_card(db, "PH")
    assert first is not None
    assert first.id == card.id
    assert vcc_pool_scheduler.acquire_card(db, "PH") is None
    vcc_pool_scheduler.release_reservation(db, first)
    again = vcc_pool_scheduler.acquire_card(db, "PH")
    assert again is not None
    vcc_pool_scheduler.release_reservation(db, again)


def _code_and_task(db, state):
    code = ActivationCode(
        code=generate_code(),
        sku_type=SkuType.CHATGPT_PLUS_1M,
        channel_mode=ChannelMode.UPSTREAM,
        status=CodeStatus.LOCKED,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    session_raw = json.dumps(
        {
            "user": {"id": "user-1", "email": "user@example.com"},
            "expires": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z",
            "accessToken": "token",
        }
    )
    task = RechargeTask(
        task_id=str(uuid4()),
        code_id=code.id,
        target_email="user@example.com",
        target_user_id="user-1",
        encrypted_session_raw=encrypt_text(session_raw),
        session_kind="chatgpt",
        state=state,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return code, task


def test_redelivery_after_payment_submission_goes_to_manual_review(db):
    code, task = _code_and_task(db, RechargeState.PAYING)
    run_recharge_task(task.task_id)
    db.expire_all()
    task = db.get(RechargeTask, task.task_id)
    code = db.get(ActivationCode, code.id)
    assert task.state == RechargeState.FAILED
    assert task.error_code == "PAYMENT_OUTCOME_UNKNOWN"
    assert code.status == CodeStatus.LOCKED
    with pytest.raises(Exception) as exc:
        retry_task(db, task.task_id)
    assert getattr(exc.value, "code", "") == "MANUAL_REVIEW_REQUIRED"
