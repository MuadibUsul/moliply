"""Per-card egress, isolated checkout context and the JIT delivery pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models import (
    ChannelMode,
    CodeStatus,
    DeliveryType,
    FreeAccountPool,
    FreeAccountStatus,
    FulfillmentStrategy,
    JitAccountJob,
    JitJobState,
    OrderDelivery,
    OrderItem,
    OrderStatus,
    Product,
    ProductCategory,
    RechargeState,
    SkuType,
    VccCard,
    VccStatus,
    utcnow,
    Order,
)
from app.services.crypto import encrypt_text
from app.services.fulfillment import context_profile, jit_delivery


# ---------------------------------------------------------------------------
# Proxy / context profile
# ---------------------------------------------------------------------------


def test_parse_proxy_splits_credentials():
    parsed = context_profile.parse_proxy("http://user:p%40ss@1.2.3.4:8080")
    assert parsed.server == "http://1.2.3.4:8080"
    assert parsed.username == "user"
    assert parsed.password == "p@ss"
    assert parsed.as_playwright() == {
        "server": "http://1.2.3.4:8080",
        "username": "user",
        "password": "p@ss",
    }


def test_socks5_with_credentials_becomes_socks5h():
    parsed = context_profile.parse_proxy("socks5://u:p@10.0.0.1:1080")
    assert parsed.server.startswith("socks5h://")
    assert parsed.username == "u"


def test_plain_socks5_keeps_its_scheme():
    assert context_profile.parse_proxy("socks5://10.0.0.1:1080").server == "socks5://10.0.0.1:1080"


def test_empty_proxy_is_none_and_bad_scheme_raises():
    assert context_profile.parse_proxy("") is None
    assert context_profile.parse_proxy(None) is None
    with pytest.raises(ValueError):
        context_profile.parse_proxy("ftp://1.2.3.4:21")
    with pytest.raises(ValueError):
        context_profile.parse_proxy("http://no-port-here")


def test_masked_proxy_hides_credentials():
    assert context_profile.mask_proxy("http://user:secret@1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_locale_and_timezone_follow_the_billing_region():
    ph = context_profile.profile_for("PH")
    us = context_profile.profile_for("US")
    assert (ph.timezone_id, ph.locale) == ("Asia/Manila", "en-PH")
    assert (us.timezone_id, us.locale) == ("America/New_York", "en-US")


def test_card_overrides_win_over_region_defaults():
    profile = context_profile.profile_for_card({"timezone": "America/Chicago", "locale": "en-CA"}, "US")
    assert profile.timezone_id == "America/Chicago"
    assert profile.locale == "en-CA"


def test_resolve_prefers_the_card_proxy():
    assert context_profile.resolve_card_proxy("socks5://a:1", "http://b:2") == ("socks5://a:1", "card")
    assert context_profile.resolve_card_proxy(None, "http://b:2") == ("http://b:2", "region")
    assert context_profile.resolve_card_proxy(None, "") == (None, "none")


# ---------------------------------------------------------------------------
# Checkout context wiring
# ---------------------------------------------------------------------------


def test_checkout_context_uses_card_timezone_and_inline_proxy(monkeypatch):
    """The browser context must carry the card's own locale/exit, not a global."""
    from app.services.fulfillment import self_vcc_worker

    captured: dict = {}

    class FakePage:
        def goto(self, *_a, **_k):
            return None

        def wait_for_timeout(self, *_a, **_k):
            return None

        @property
        def frames(self):
            return []

        def locator(self, *_a, **_k):
            class L:
                first = self
                count = lambda self: 1  # noqa: E731
                fill = lambda self, *a, **k: None  # noqa: E731
                click = lambda self, *a, **k: None  # noqa: E731
                is_visible = lambda self: True  # noqa: E731
                evaluate = lambda self, *a, **k: "input"  # noqa: E731
                select_option = lambda self, *a, **k: None  # noqa: E731

            return L()

    class FakeContext:
        def __init__(self):
            self.init_scripts = []
            self.kwargs = {}

        def new_page(self):
            return FakePage()

        def add_init_script(self, script):
            self.init_scripts.append(script)

        def close(self):
            return None

    class FakeBrowser:
        def __init__(self):
            self.context = FakeContext()
            self.launch_kwargs = {}

        def new_context(self, **kwargs):
            self.context.kwargs = kwargs
            return self.context

        def close(self):
            return None

    class FakeChromium:
        def __init__(self):
            self.browser = FakeBrowser()

        def launch(self, **kwargs):
            self.browser.launch_kwargs = kwargs
            return self.browser

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake = FakePlaywright()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: fake)
    monkeypatch.setattr(self_vcc_worker.runtime, "get_list", lambda *a, **k: ["chatgpt.com"])

    driver = self_vcc_worker.PlaywrightCheckout(headless=True)
    # Stop right after the form fill so the context is observable.
    monkeypatch.setattr(driver, "_click_first", lambda *_a, **_k: False)
    with pytest.raises(self_vcc_worker.SelfVccFailure):
        driver.run(
            "https://chatgpt.com/checkout",
            {
                "number": "4242424242424242",
                "cvv": "123",
                "exp_month": "12",
                "exp_year": "29",
                "holder_name": "Demo",
                "billing": {"postal_code": "10001", "line1": "1 Main", "city": "NY", "state": "NY", "country": "US"},
                "timezone": "Asia/Manila",
                "locale": "en-PH",
            },
            "http://user:secret@1.2.3.4:8080",
            region="PH",
        )

    context_kwargs = fake.chromium.browser.context.kwargs
    assert context_kwargs["timezone_id"] == "Asia/Manila"
    assert context_kwargs["locale"] == "en-PH"
    assert "Accept-Language" in context_kwargs["extra_http_headers"]
    assert any("webdriver" in script for script in fake.chromium.browser.context.init_scripts)
    assert fake.chromium.browser.launch_kwargs["proxy"]["username"] == "user"
    assert fake.chromium.browser.launch_kwargs["proxy"]["password"] == "secret"


def test_checkout_without_any_proxy_is_rejected(monkeypatch):
    from app.services.fulfillment import self_vcc_worker

    monkeypatch.setattr(self_vcc_worker.runtime, "get_list", lambda *a, **k: ["chatgpt.com"])
    driver = self_vcc_worker.PlaywrightCheckout()
    with pytest.raises(self_vcc_worker.SelfVccFailure) as exc:
        driver.run("https://chatgpt.com/checkout", {"billing": {}}, "", region="US")
    assert exc.value.code == "PROXY_NOT_CONFIGURED"


def test_invalid_card_proxy_is_reported(monkeypatch):
    from app.services.fulfillment import self_vcc_worker

    monkeypatch.setattr(self_vcc_worker.runtime, "get_list", lambda *a, **k: ["chatgpt.com"])
    driver = self_vcc_worker.PlaywrightCheckout()
    with pytest.raises(self_vcc_worker.SelfVccFailure) as exc:
        driver.run("https://chatgpt.com/checkout", {"billing": {}}, "ftp://x:1", region="US")
    assert exc.value.code == "PROXY_INVALID"


# ---------------------------------------------------------------------------
# JIT delivery
# ---------------------------------------------------------------------------


def _jit_product(db, slug="jit-probe") -> Product:
    product = Product(
        slug=slug,
        name="JIT Probe",
        subtitle="probe",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="ChatGPT",
        logo_key="gpt",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price=Decimal("198.00"),
        active=True,
        fulfillment_strategy=FulfillmentStrategy.JIT_ACCOUNT_RECHARGE,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def _paid_order_item(db, product) -> tuple[Order, OrderItem]:
    order = Order(order_no=f"JIT{utcnow().strftime('%H%M%S%f')}", email="jit@example.com", amount=Decimal("198.00"), status=OrderStatus.PAID)
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        product_name=product.name,
        unit_price=Decimal("198.00"),
        quantity=1,
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return order, item


def _free_account(db, product, email="free@example.com", session=True) -> FreeAccountPool:
    raw = ""
    if session:
        raw = json.dumps(
            {
                "user": {"id": "u-1", "email": email},
                "expires": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z",
                "accessToken": "tok",
                "account": {"planType": "free"},
            }
        )
    account = FreeAccountPool(
        product_id=product.id,
        email=email,
        password_enc=encrypt_text("FreePass!123"),
        recovery_enc=encrypt_text("backup@example.com"),
        session_raw_enc=encrypt_text(raw) if raw else None,
        status=FreeAccountStatus.AVAILABLE,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_claim_locks_exactly_one_account(db):
    product = _jit_product(db)
    _free_account(db, product, "a@example.com")
    _free_account(db, product, "b@example.com")
    _, item = _paid_order_item(db, product)

    claimed = jit_delivery.claim_free_account(db, product.id, item.id)
    assert claimed.email == "a@example.com"
    assert claimed.status == FreeAccountStatus.LOCKED
    assert jit_delivery.available_free_accounts(db, product.id) == 1


def test_empty_pool_raises(db):
    product = _jit_product(db, slug="jit-empty")
    _, item = _paid_order_item(db, product)
    with pytest.raises(jit_delivery.JitError) as exc:
        jit_delivery.claim_free_account(db, product.id, item.id)
    assert exc.value.code == "JIT_POOL_EMPTY"


def test_release_puts_the_account_back(db):
    product = _jit_product(db, slug="jit-release")
    account = _free_account(db, product)
    _, item = _paid_order_item(db, product)
    jit_delivery.claim_free_account(db, product.id, item.id)
    jit_delivery.release_free_account(db, account)
    db.refresh(account)
    assert account.status == FreeAccountStatus.AVAILABLE
    assert account.order_item_id is None


def test_successful_job_delivers_credentials_and_closes_the_order(db, monkeypatch):
    product = _jit_product(db, slug="jit-success")
    _free_account(db, product, "deliver-me@example.com")
    order, item = _paid_order_item(db, product)

    # The recharge engine is exercised separately; here it must simply succeed.
    from app.services import redeem as redeem_service

    def fake_run(task_id):
        db2 = __import__("app.database", fromlist=["SessionLocal"]).SessionLocal()
        try:
            from app.models import RechargeTask

            task = db2.get(RechargeTask, task_id)
            task.state = RechargeState.SUCCESS
            task.completed_at = utcnow()
            db2.commit()
        finally:
            db2.close()

    monkeypatch.setattr(redeem_service, "run_recharge_task", fake_run)

    job = JitAccountJob(order_item_id=item.id, state=JitJobState.PENDING)
    db.add(job)
    db.commit()
    jit_delivery.run_jit_job(job.id)

    db.expire_all()
    job = db.get(JitAccountJob, job.id)
    assert job.state == JitJobState.DELIVERED
    delivery = db.query(OrderDelivery).filter(OrderDelivery.order_item_id == item.id).one()
    from app.services.crypto import decrypt_text

    payload = decrypt_text(delivery.payload_enc)
    assert payload.startswith("deliver-me@example.com----")
    order = db.get(Order, order.id)
    assert order.status == OrderStatus.DELIVERED


def test_missing_session_fails_the_job_and_returns_the_account(db, monkeypatch):
    product = _jit_product(db, slug="jit-nosession")
    account = _free_account(db, product, "no-session@example.com", session=False)
    _, item = _paid_order_item(db, product)

    job = JitAccountJob(order_item_id=item.id, state=JitJobState.PENDING)
    db.add(job)
    db.commit()
    jit_delivery.run_jit_job(job.id)

    db.expire_all()
    job = db.get(JitAccountJob, job.id)
    account = db.get(FreeAccountPool, account.id)
    assert job.state == JitJobState.FAILED
    assert job.error_code == "JIT_SESSION_MISSING"
    assert account.status == FreeAccountStatus.AVAILABLE


def test_recharge_failure_releases_the_account_and_keeps_the_order_pending(db, monkeypatch):
    product = _jit_product(db, slug="jit-fail")
    account = _free_account(db, product, "fails@example.com")
    order, item = _paid_order_item(db, product)

    from app.services import redeem as redeem_service

    def fake_run(task_id):
        db2 = __import__("app.database", fromlist=["SessionLocal"]).SessionLocal()
        try:
            from app.models import RechargeTask

            task = db2.get(RechargeTask, task_id)
            task.state = RechargeState.FAILED
            task.error_code = "CARD_DECLINED"
            task.error_message = "拒付"
            db2.commit()
        finally:
            db2.close()

    monkeypatch.setattr(redeem_service, "run_recharge_task", fake_run)

    job = JitAccountJob(order_item_id=item.id, state=JitJobState.PENDING)
    db.add(job)
    db.commit()
    jit_delivery.run_jit_job(job.id)

    db.expire_all()
    assert db.get(JitAccountJob, job.id).state == JitJobState.FAILED
    assert db.get(FreeAccountPool, account.id).status == FreeAccountStatus.AVAILABLE
    assert db.get(Order, order.id).status == OrderStatus.PAID


def test_jit_item_goes_stock_pending_instead_of_failing_payment(db, monkeypatch):
    """A JIT line must not be treated as an empty account shelf."""
    from app.services import orders as order_service
    from app.services.fulfillment import jit_delivery as jit_module

    product = _jit_product(db, slug="jit-fulfil")
    order, item = _paid_order_item(db, product)
    queued: list[int] = []
    monkeypatch.setattr(jit_module, "start_jit_delivery", lambda _db, i: queued.append(i.id) or None)

    order_service.fulfill_order(db, order)

    db.expire_all()
    order = db.get(Order, order.id)
    assert order.status == OrderStatus.PAID
    assert order.stock_pending is True
    assert queued == [item.id]


def test_jit_product_stock_reads_the_free_pool(client, db):
    from app.services import catalog

    product = _jit_product(db, slug="jit-stock")
    _free_account(db, product, "one@example.com")
    payload = catalog.serialize_product(db, product)
    assert payload["stock"] == 1
    assert payload["fulfillment_strategy"] == FulfillmentStrategy.JIT_ACCOUNT_RECHARGE.value


def test_admin_can_import_free_accounts(client, db, admin_headers):
    product = _jit_product(db, slug="jit-import")
    response = client.post(
        "/api/v1/admin/free-accounts/import",
        headers=admin_headers,
        json={"product_id": product.id, "text": "imp@example.com----pw----rec@example.com"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1

    listing = client.get("/api/v1/admin/free-accounts", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.json()["available"] >= 1


def test_free_account_import_rejects_a_stock_product(client, db, admin_headers):
    product = Product(
        slug="plain-account-probe",
        name="Plain",
        subtitle="x",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="x",
        logo_key="gpt",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        price=Decimal("1.00"),
        active=True,
    )
    db.add(product)
    db.commit()
    response = client.post(
        "/api/v1/admin/free-accounts/import",
        headers=admin_headers,
        json={"product_id": product.id, "text": "a@example.com----pw"},
    )
    assert response.status_code == 409


def test_seeded_jit_product_uses_the_jit_strategy(client):
    product = client.get("/api/v1/store/products/chatgpt-plus-jit-account").json()["product"]
    assert product["fulfillment_strategy"] == FulfillmentStrategy.JIT_ACCOUNT_RECHARGE.value
    assert product["stock"] >= 1


def test_disabling_the_engine_falls_back_to_finished_stock(db, monkeypatch):
    """Killing the JIT switch must degrade to ordinary account stock, not break."""
    from app.models import AccountInventory, InventoryStatus
    from app.services import catalog

    product = _jit_product(db, slug="jit-switch-off")
    _free_account(db, product, "pooled@example.com")
    db.add(
        AccountInventory(
            product_id=product.id,
            label="finished@example.com",
            payload_enc=encrypt_text("finished@example.com----pw"),
            status=InventoryStatus.AVAILABLE,
        )
    )
    db.commit()

    monkeypatch.setattr(jit_delivery, "jit_enabled", lambda: True)
    assert catalog.serialize_product(db, product)["stock"] == 1  # the free pool

    monkeypatch.setattr(jit_delivery, "jit_enabled", lambda: False)
    assert catalog.serialize_product(db, product)["stock"] == 1  # the finished account
