from __future__ import annotations

from decimal import Decimal

import pytest

from app.models import VccCard, VccStatus
from app.services.crypto import encrypt_text
from app.services.fulfillment import vcc_pool_scheduler
from app.services.fulfillment.self_vcc_worker import mark_card_success, select_vcc_card, suspend_card


@pytest.fixture(autouse=True)
def _suspend_pre_existing_cards(db):
    db.query(VccCard).update({VccCard.status: VccStatus.SUSPENDED})
    db.commit()
    yield


def _card(db, balance, status=VccStatus.ACTIVE, binds=0, max_binds=1, failed=0):
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
        available_balance=Decimal(str(balance)),
        max_bind_count=max_binds,
        current_bind_count=binds,
        failed_count=failed,
        status=status,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def test_select_card_respects_balance_bind_and_failure_constraints(db):
    _card(db, 10)
    low = _card(db, 21.99)
    good = _card(db, 50)
    _card(db, 100, status=VccStatus.SUSPENDED)
    _card(db, 100, binds=1, max_binds=1)
    _card(db, 100, failed=1)

    selected = select_vcc_card(db)
    assert selected is not None
    assert selected.id == good.id
    assert selected.id not in {low.id}


def test_repeated_declines_eventually_suspend_card(db):
    card = _card(db, 50)
    for _ in range(vcc_pool_scheduler.max_consecutive_failures()):
        suspend_card(db, card)
    db.refresh(card)
    assert card.status == VccStatus.SUSPENDED
    assert card.consecutive_failures == vcc_pool_scheduler.max_consecutive_failures()
    assert select_vcc_card(db) is None


def test_hard_decline_suspends_immediately(db):
    card = _card(db, 50)
    vcc_pool_scheduler.suspend(db, card, "card_declined: stolen card", hard=True)
    db.refresh(card)
    assert card.status == VccStatus.SUSPENDED


def test_success_consumes_the_reservation(db):
    card = _card(db, 25, max_binds=1)
    reserved = select_vcc_card(db)
    assert reserved is not None
    assert reserved.reserved_balance > Decimal("0")
    # The reservation makes the card unavailable to a second worker.
    assert select_vcc_card(db) is None

    mark_card_success(db, reserved)
    db.refresh(card)
    assert card.current_bind_count == 1
    assert card.reserved_balance == Decimal("0.00")
    assert card.status == VccStatus.EXHAUSTED
    assert card.available_balance < Decimal("25")
